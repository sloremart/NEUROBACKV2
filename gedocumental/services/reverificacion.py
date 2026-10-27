"""
Seguimiento de hallazgos de la IA SIN volver a llamar a la IA (sin costo de OpenAI).

Cada vez que corre (cron revisar_resultados, o el comando reverificar_hallazgos) toma los hallazgos
pendientes y los cierra cuando el sistema puede comprobar, con reglas fijas, que ya no aplican:

  CORREGIDO  — alguien corrigió el dato en ZeusSalud y ahora coincide con el documento
               (nombre o número de documento del paciente, paciente de la admisión).
  DESCARTADO — validación incorrecta de la IA: el PDF tiene texto y en él está el dato correcto
               (el número de documento o el nombre de Zeus) y NO está lo que la IA dijo haber leído;
               o el comprobante sí tiene la firma; o el RESULTADO sí documenta el CUPS de la admisión.

  DESCARTADO — también: error de escritura del nombre o número en una ORDEN/HC (documento de un tercero)
               cuando la AUTORIZACIÓN de la EPS coincide con Zeus.
               O el nombre/número leído en un documento no lo confirma ningún otro documento de la
               admisión (cada escaneo "dice" algo distinto): es error de lectura, no del paciente.

Las reglas con Zeus se evalúan siempre (no abren el PDF). Las que leen el PDF solo se aplican a PDFs
digitales (todas las páginas con texto): si hay una página escaneada, solo la IA puede volver a leerla
y el hallazgo queda en la lista "requieren IA". Cada PDF se relee como máximo cada
REVERIFICAR_PDF_CADA_HORAS y cada ejecución tiene un tope de tiempo de lectura.
Los hallazgos de recaudo los sigue reverificar_recaudo_con_zeus (ai_revision.py).
"""

import logging
import os
import re
import time
from datetime import timedelta

from gedocumental.services.ai_revision import (
    MAX_PAGINAS_LEER, MIN_CARACTERES_TEXTO, TIPOS_SIN_REVISION_IA, _buscar_palabra, _codigos_cups,
    _consulta_documentada, _detectar_firma_pdf, _es_comprobante, _mismo_codigo_cups, _mismo_paciente,
    _nombre_casi_igual, _separar_pegadas, TIPOS_SOLO_PACIENTE,
    _procedimiento_documentado, _solo_motivo_procedimiento, _tokens_nombre, _NO_SON_NOMBRE,
    obtener_contexto_admision,
)

logger = logging.getLogger(__name__)

CORREGIDO, DESCARTADO = 'CORREGIDO', 'DESCARTADO'
REVERIFICAR_PDF_CADA_HORAS = 24
MAX_SEGUNDOS_PDF = 120          # tope de lectura de PDFs por ejecución
MAX_SEGUNDOS_POR_PDF = 20

_VALOR_DOCUMENTO = re.compile(r"documento '(?P<doc>[^']*)'")
DOC_INCORRECTO = 'Número de documento incorrecto'
NOMBRE_INCORRECTO = 'Nombre del usuario incorrecto'
PACIENTE_NO_CORRESPONDE = 'Paciente no corresponde: documento'
PACIENTE_SIN_DATOS = 'Paciente no corresponde: el documento no contiene'

# Documentos que escribe un tercero (médico u hospital que ordena): un error de escritura en el nombre o
# el número del paciente no lo puede corregir facturación. Si la AUTORIZACIÓN de la EPS coincide con Zeus,
# el error es del tercero y no es hallazgo; si la autorización dice lo mismo que la orden, el error es de Zeus.
TIPOS_DE_TERCEROS = ('ORDEN', 'HCLINICA', 'HCNEURO', 'ADICIONALES')


# ── Lectura del PDF (solo texto, sin imágenes ni IA) ─────────────────────────

def texto_pdf(ruta_absoluta: str) -> tuple[str, bool]:
    """
    (texto, completo). completo=False si alguna página no tiene texto (escaneada o foto) o no se pudo
    leer todo: en ese caso el texto NO alcanza para descartar lo que la IA leyó en la imagen.
    """
    if not ruta_absoluta or not ruta_absoluta.lower().endswith('.pdf') or not os.path.exists(ruta_absoluta):
        return '', False
    try:
        import pdfplumber
        textos, completo = [], True
        inicio = time.monotonic()
        with pdfplumber.open(ruta_absoluta) as pdf:
            if len(pdf.pages) > MAX_PAGINAS_LEER:
                completo = False
            for pagina in pdf.pages[:MAX_PAGINAS_LEER]:
                if time.monotonic() - inicio > MAX_SEGUNDOS_POR_PDF:
                    completo = False
                    break
                t = (pagina.extract_text() or '').strip()
                if len(t) < MIN_CARACTERES_TEXTO:
                    completo = False
                textos.append(t)
                if hasattr(pagina, 'close'):
                    pagina.close()
        return '\n'.join(textos), completo
    except Exception as exc:
        logger.warning("No se pudo leer el texto de %s: %s", ruta_absoluta, exc)
        return '', False


def numeros_en_texto(texto: str) -> set:
    """Números de 5+ dígitos del texto, sin separadores de miles ('1.121.978.991' → '1121978991')."""
    plano = re.sub(r'(?<=\d)[.,](?=\d{3}(?!\d))', '', texto or '')
    return set(re.findall(r'\d{5,}', plano)) | set(re.findall(r'\d{5,}', texto or ''))


def _digitos(valor) -> str:
    return re.sub(r'\D', '', str(valor or ''))


def _nombre_en_texto(tokens: list, texto_tokens: list) -> bool:
    return len(tokens) >= 2 and all(_buscar_palabra(t, texto_tokens) for t in tokens)


def _tokens_doc(nombre) -> list:
    return [t for t in _tokens_nombre(nombre) if t not in _NO_SON_NOMBRE]


def _plano(descripcion) -> str:
    import unicodedata
    return unicodedata.normalize('NFKD', str(descripcion or '')).encode('ascii', 'ignore').decode().lower()


def _solo_motivo_firma(descripcion) -> bool:
    d = _plano(descripcion)
    otros = re.search(r'paciente no|nombre|documento de identidad|identificacion|numero de documento|'
                      r'procedimiento|cups|vencid|fecha|legib|cantidad|autorizacion', d)
    return 'firma' in d and not otros


def _menciona_lateralidad(descripcion) -> bool:
    return bool(re.search(r'derech|izquierd|lateralidad|bilateral', _plano(descripcion)))


def _numero_parecido(a: str, b: str) -> bool:
    """Mismo número con un error de escritura (dígito cambiado, de más o de menos), no otro número."""
    from difflib import SequenceMatcher
    return (len(a) >= 6 and len(b) >= 6 and abs(len(a) - len(b)) <= 1
            and SequenceMatcher(None, a, b).ratio() >= 0.75)


def lectura_autorizacion(admision_id, datos_admision: dict) -> dict | None:
    """
    Qué dice del paciente la AUTORIZACIÓN de la admisión, según su última revisión IA:
    {'nombre': ..., 'documento': ...}. Sin hallazgo de paciente pendiente → los datos de Zeus (coincide).
    None si no hay autorización revisada o su hallazgo de paciente no permite saberlo.
    """
    from gedocumental.models import RevisionIA
    r = (RevisionIA.objects.filter(AdmisionId=admision_id, TipoDocumento__iexact='AUTORIZACION',
                                   IdArchivo__isnull=False).order_by('-IdRevision').first())
    return _lectura_de_revision(r, datos_admision) if r else None


def _lectura_de_revision(r, datos_admision: dict) -> dict | None:
    """
    Qué leyó la IA del paciente en una revisión: {'nombre', 'documento'} (None = no se sabe).
    Sin hallazgo de nombre/número → los datos de Zeus (la IA los aceptó). Los hallazgos cerrados conservan
    lo que se leyó, salvo los descartados porque el PDF digital mostró que la IA leyó mal ('El PDF ...').
    """
    zeus = {'nombre': datos_admision.get('paciente') or '', 'documento': _digitos(datos_admision.get('identificacion'))}
    d = r.Descripcion or ''
    if r.TipoHallazgo != 'OK' and re.search(r'Paciente en documento|paciente no coincide', d, re.IGNORECASE):
        return None   # formato antiguo de "otra persona": no dice qué número/nombre leyó
    if (r.TipoHallazgo == 'OK'
            or (r.Resuelta and getattr(r, 'Resolucion', None) not in (DESCARTADO, CORREGIDO))
            or (getattr(r, 'Resolucion', None) == DESCARTADO
                and (getattr(r, 'NotaResolucion', '') or '').startswith('El PDF'))
            or not re.search(r'Nombre del usuario incorrecto|Número de documento incorrecto|Paciente no corresponde',
                             d)):
        return zeus
    if ' | ' in d or d.startswith('Paciente no corresponde'):
        return None
    m = _VALOR_DOCUMENTO.search(d)
    if not m:
        return None
    if d.startswith(NOMBRE_INCORRECTO):
        return {'nombre': m.group('doc'), 'documento': None}   # el número no se sabe
    return {'nombre': zeus['nombre'], 'documento': _digitos(m.group('doc'))}


def lecturas_admision(admision_id, datos_admision: dict) -> dict:
    """{IdArchivo: {'tipo', 'nombre', 'documento'}}: lo que leyó la IA del paciente en cada archivo vigente."""
    from gedocumental.models import RevisionIA
    lecturas = {}
    for r in (RevisionIA.objects.filter(AdmisionId=admision_id, IdArchivo__isnull=False)
              .exclude(TipoDocumento__in=TIPOS_SIN_REVISION_IA).order_by('IdArchivo', '-IdRevision')):
        if r.IdArchivo_id in lecturas:
            continue   # solo la última revisión de cada archivo
        lectura = _lectura_de_revision(r, datos_admision)
        lecturas[r.IdArchivo_id] = dict(lectura, tipo=r.TipoDocumento) if lectura else None
    return {k: v for k, v in lecturas.items() if v}


def necesita_autorizacion(tipo_documento: str, descripcion: str) -> bool:
    return ((tipo_documento or '').upper() in TIPOS_DE_TERCEROS
            and (descripcion or '').startswith((DOC_INCORRECTO, NOMBRE_INCORRECTO)))


def necesita_pdf(tipo_documento: str, descripcion: str) -> bool:
    """True si alguna regla de evaluar_hallazgo puede usar el PDF para este hallazgo."""
    d = descripcion or ''
    if d.startswith((DOC_INCORRECTO, NOMBRE_INCORRECTO, PACIENTE_NO_CORRESPONDE, PACIENTE_SIN_DATOS)):
        return True
    if _es_comprobante(tipo_documento) and _solo_motivo_firma(d):
        return True
    return _solo_motivo_procedimiento(d) and not _menciona_lateralidad(d)


# ── Reglas ───────────────────────────────────────────────────────────────────

def _resumen_lecturas(lecturas: list, campo: str) -> str:
    return "; ".join(f"{x.get('tipo', '')}: {x.get(campo)}" for x in lecturas[:4])


def _solo_paciente(leido):
    # Historia clínica y soportes: solo importa que sean del paciente (otra persona sigue siendo hallazgo)
    return (DESCARTADO, "En la historia clínica y los soportes solo se valida que sean del paciente; "
                        f"la diferencia de escritura ('{leido}') no es hallazgo.")


def evaluar_hallazgo(tipo_documento: str, descripcion: str, datos_admision: dict,
                     texto: str | None = None, firma: bool | None = None,
                     autorizacion: dict | None = None, otras: list | None = None) -> tuple[str, str] | None:
    """
    Decide si un hallazgo pendiente ya no aplica. Devuelve (resolución, nota) o None si sigue abierto.
      texto: texto COMPLETO del PDF (solo PDFs digitales); None si no se leyó o es escaneado.
      firma: resultado de _detectar_firma_pdf (solo comprobantes).
      autorizacion: lectura_autorizacion() de la admisión (para órdenes y HC de terceros).
      otras: lo que leyó la IA del paciente en los DEMÁS documentos de la admisión (lecturas_admision).
    No toca la base de datos.
    """
    d = (descripcion or '').strip()
    if not d or ' | ' in d:
        return None   # varios motivos en un hallazgo: no se cierra por uno solo
    tipo = (tipo_documento or '').upper()
    id_adm = _digitos(datos_admision.get('identificacion'))
    tokens_adm = _tokens_nombre(datos_admision.get('paciente'))
    m = _VALOR_DOCUMENTO.search(d)
    leido = m.group('doc') if m else ''
    texto_tokens = _tokens_nombre(texto) if texto else []
    numeros = numeros_en_texto(texto) if texto else set()

    if d.startswith(DOC_INCORRECTO) and leido:
        id_doc = _digitos(leido)
        if id_doc and id_doc == id_adm:
            return CORREGIDO, f"ZeusSalud ya tiene el número de documento del archivo ({id_doc})."
        if tipo in TIPOS_SOLO_PACIENTE:
            return _solo_paciente(leido)
        if (tipo in TIPOS_DE_TERCEROS and autorizacion and id_adm and _numero_parecido(id_doc, id_adm)
                and autorizacion.get('documento') == id_adm):
            return DESCARTADO, (f"Error de escritura del número en el documento del tercero ({id_doc}); "
                                f"la autorización de la EPS coincide con ZeusSalud ({id_adm}).")
        if otras and id_adm and _numero_parecido(id_doc, id_adm):
            conocidas = [x for x in otras if x.get('documento')]
            if conocidas and not any(x['documento'] == id_doc for x in conocidas):
                return DESCARTADO, (f"Ningún otro documento de la admisión tiene el número {id_doc} "
                                    f"({_resumen_lecturas(conocidas, 'documento')}); ZeusSalud: {id_adm}. "
                                    "Error de lectura del escaneo o de escritura de un solo documento.")
        if texto and id_adm and id_adm in numeros and id_doc not in numeros:
            return DESCARTADO, (f"El PDF tiene el número de la admisión ({id_adm}) y no el que leyó la IA "
                                f"({id_doc}).")
        return None

    if d.startswith(NOMBRE_INCORRECTO) and leido:
        if _mismo_paciente({'nombre_documento': leido, 'identificacion_documento': ''}, datos_admision):
            return CORREGIDO, (f"El nombre del archivo ('{leido}') coincide con ZeusSalud (corregido en Zeus o "
                               f"diferencia solo de formato).")
        if tipo in TIPOS_SOLO_PACIENTE:
            return _solo_paciente(leido)
        if tipo in TIPOS_DE_TERCEROS and autorizacion and _nombre_casi_igual(_tokens_doc(leido), tokens_adm):
            distintas = [t for t in _separar_pegadas(_tokens_doc(leido), tokens_adm)
                         if not _buscar_palabra(t, tokens_adm)]
            tokens_aut = _tokens_nombre(autorizacion.get('nombre'))
            if distintas and tokens_aut and not any(_buscar_palabra(t, tokens_aut) for t in distintas):
                return DESCARTADO, (f"Error de escritura del nombre en el documento del tercero ('{leido}'); "
                                    f"la autorización de la EPS coincide con ZeusSalud "
                                    f"({datos_admision.get('paciente')}).")
        if otras:
            palabras = _separar_pegadas(_tokens_doc(leido), tokens_adm)
            distintas = [t for t in palabras if not _buscar_palabra(t, tokens_adm)]
            conocidas = [x for x in otras if len(_tokens_nombre(x.get('nombre'))) >= 2]
            nadie_repite = distintas and conocidas and not any(
                _buscar_palabra(t, _tokens_nombre(x['nombre'])) for x in conocidas for t in distintas)
            # Otro documento de la admisión tiene el nombre de Zeus (la IA lo aceptó o lo leyó igual)
            confirman_zeus = [x for x in conocidas
                              if _mismo_paciente({'nombre_documento': x['nombre'], 'identificacion_documento': ''},
                                                 datos_admision)]
            # Casi igual letra por letra (RODRIGEZ / RODRIGUEZ), o una sola palabra distinta y al menos dos
            # iguales (SANTANA BEATRIZ / SANTANA BENITO) cuando otro documento confirma el nombre de Zeus
            una_palabra = len(distintas) == 1 and len(palabras) - 1 >= 2 and confirman_zeus
            if nadie_repite and (_nombre_casi_igual(_tokens_doc(leido), tokens_adm) or una_palabra):
                return DESCARTADO, (f"Ningún otro documento de la admisión escribe el nombre como '{leido}' "
                                    f"({_resumen_lecturas(conocidas, 'nombre')}); ZeusSalud: "
                                    f"{datos_admision.get('paciente')}. Error de lectura del escaneo o de "
                                    "escritura de un solo documento.")
        if texto and _nombre_en_texto(tokens_adm, texto_tokens):
            distintas = [t for t in _tokens_doc(leido) if not _buscar_palabra(t, tokens_adm)]
            if distintas and not any(_buscar_palabra(t, texto_tokens) for t in distintas):
                return DESCARTADO, (f"El PDF tiene el nombre de la admisión ({datos_admision.get('paciente')}); "
                                    f"la IA leyó '{leido}'.")
        return None

    if d.startswith(PACIENTE_NO_CORRESPONDE) and leido:
        if id_adm and _mismo_paciente({'nombre_documento': leido, 'identificacion_documento': ''}, datos_admision):
            return CORREGIDO, f"La admisión ya corresponde al paciente del archivo ({leido})."
        if (texto and id_adm and id_adm in numeros and _nombre_en_texto(tokens_adm, texto_tokens)
                and not _nombre_en_texto(_tokens_doc(leido), texto_tokens)):
            return DESCARTADO, (f"El PDF tiene el nombre y el documento de la admisión "
                                f"({datos_admision.get('paciente')}, {id_adm}); la IA leyó '{leido}'.")
        return None

    if d.startswith(PACIENTE_SIN_DATOS):
        if texto and ((id_adm and id_adm in numeros) or _nombre_en_texto(tokens_adm, texto_tokens)):
            return CORREGIDO, "El PDF ya tiene el nombre o el documento del paciente de la admisión."
        return None

    if _es_comprobante(tipo) and firma is True and _solo_motivo_firma(d):
        return DESCARTADO, "El sistema detecta la firma del paciente en el PDF."

    if texto and _solo_motivo_procedimiento(d) and not _menciona_lateralidad(d):
        if tipo == 'RESULTADO' and (_consulta_documentada(texto, datos_admision)
                                    or _procedimiento_documentado(texto, datos_admision)):
            return DESCARTADO, "El PDF documenta el procedimiento del CUPS de la admisión."
        if _mismo_codigo_cups(texto, '', datos_admision):
            codigos = sorted(_codigos_cups(texto))
            return DESCARTADO, f"El PDF tiene el mismo código CUPS de la admisión ({', '.join(codigos[:3])})."
    return None


# ── Ejecución sobre los hallazgos pendientes ─────────────────────────────────

def _pendientes(admision=None, tipo_documento=None):
    from gedocumental.models import RevisionIA
    qs = (RevisionIA.objects.filter(Resuelta=False).exclude(TipoHallazgo='OK')
          .exclude(TipoDocumento__in=TIPOS_SIN_REVISION_IA).select_related('IdArchivo'))
    if admision:
        qs = qs.filter(AdmisionId=admision)
    if tipo_documento:
        qs = qs.filter(TipoDocumento__iexact=tipo_documento)
    # Primero los que nunca se releyeron (NULL va primero en MySQL)
    return qs.order_by('FechaReverificacion', 'IdRevision')


def _ruta(h) -> str:
    try:
        return h.IdArchivo.RutaArchivo.path if h.IdArchivo_id else ''
    except Exception:
        return ''


def reverificar_hallazgos_sin_ia(admision=None, tipo_documento=None, aplicar=True, forzar_pdf=False,
                                 max_segundos_pdf=MAX_SEGUNDOS_PDF) -> dict:
    """
    Recorre los hallazgos pendientes y cierra los que ya no aplican (ver docstring del módulo).
      aplicar=False: solo informa qué cerraría (no guarda nada).
      forzar_pdf: relee el PDF aunque se haya releído hace menos de REVERIFICAR_PDF_CADA_HORAS.
    Devuelve {"resumen": {...}, "detalle": [ {...}, ... ]}.
    """
    from django.utils import timezone

    ahora = timezone.now()
    limite_relectura = ahora - timedelta(hours=REVERIFICAR_PDF_CADA_HORAS)
    inicio = time.monotonic()
    resumen = {'revisados': 0, CORREGIDO: 0, DESCARTADO: 0, 'requieren_ia': 0, 'pdf_leidos': 0, 'sin_archivo': 0}
    detalle, contextos, autorizaciones, lecturas = [], {}, {}, {}

    for h in _pendientes(admision, tipo_documento):
        resumen['revisados'] += 1
        if h.AdmisionId not in contextos:
            contextos[h.AdmisionId] = obtener_contexto_admision(h.AdmisionId)
        datos = contextos[h.AdmisionId]
        if not datos.get('identificacion') and not datos.get('paciente'):
            continue   # Zeus no respondió: no se decide nada

        autorizacion = None
        if necesita_autorizacion(h.TipoDocumento, h.Descripcion):
            if h.AdmisionId not in autorizaciones:
                autorizaciones[h.AdmisionId] = lectura_autorizacion(h.AdmisionId, datos)
            autorizacion = autorizaciones[h.AdmisionId]
        otras = None
        if (h.Descripcion or '').startswith((DOC_INCORRECTO, NOMBRE_INCORRECTO)):
            if h.AdmisionId not in lecturas:
                lecturas[h.AdmisionId] = lecturas_admision(h.AdmisionId, datos)
            otras = [v for k, v in lecturas[h.AdmisionId].items() if k != h.IdArchivo_id]
        decision = evaluar_hallazgo(h.TipoDocumento, h.Descripcion, datos, autorizacion=autorizacion, otras=otras)
        releer = (decision is None and necesita_pdf(h.TipoDocumento, h.Descripcion)
                  and (forzar_pdf or h.FechaReverificacion is None or h.FechaReverificacion < limite_relectura)
                  and time.monotonic() - inicio < max_segundos_pdf)
        if releer:
            ruta = _ruta(h)
            texto, completo = texto_pdf(ruta)
            firma = _detectar_firma_pdf(ruta) if ruta and _es_comprobante(h.TipoDocumento) else None
            resumen['pdf_leidos'] += 1
            decision = evaluar_hallazgo(h.TipoDocumento, h.Descripcion, datos,
                                        texto=texto if completo else None, firma=firma,
                                        autorizacion=autorizacion, otras=otras)
            if aplicar:
                h.FechaReverificacion = ahora
                if not decision:
                    h.save(update_fields=['FechaReverificacion'])
            if not decision and not completo:
                existe = bool(ruta) and os.path.exists(ruta)
                resumen['requieren_ia' if existe else 'sin_archivo'] += 1
                detalle.append({'IdRevision': h.IdRevision, 'Admision': h.AdmisionId,
                                'TipoDocumento': h.TipoDocumento, 'Resolucion': None,
                                'Nota': ('Escaneado o imagen: solo la IA puede volver a leerlo' if existe
                                         else 'Archivo no encontrado en el disco'),
                                'Descripcion': h.Descripcion})

        if not decision:
            continue
        resolucion, nota = decision
        resumen[resolucion] += 1
        detalle.append({'IdRevision': h.IdRevision, 'Admision': h.AdmisionId, 'TipoDocumento': h.TipoDocumento,
                        'Resolucion': resolucion, 'Nota': nota, 'Descripcion': h.Descripcion})
        if aplicar:
            h.Resuelta = True
            h.Resolucion = resolucion
            h.FechaResolucion = ahora
            h.NotaResolucion = nota
            campos = ['Resuelta', 'Resolucion', 'FechaResolucion', 'NotaResolucion']
            if releer:
                campos.append('FechaReverificacion')
            h.save(update_fields=campos)
            logger.info("Hallazgo %s (admisión %s) cerrado sin IA como %s: %s",
                        h.IdRevision, h.AdmisionId, resolucion, nota)
    return {'resumen': resumen, 'detalle': detalle}
