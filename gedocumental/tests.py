"""
Tests sin base de datos (SimpleTestCase): reglas de reverificación de hallazgos sin IA y del recaudo.
  python manage.py test gedocumental
"""

import os
import tempfile
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase
from django.utils import timezone

from gedocumental.services import recaudo, reverificacion
from gedocumental.services.reverificacion import (
    CORREGIDO, DESCARTADO, evaluar_hallazgo, lectura_autorizacion, lecturas_admision, necesita_pdf,
    numeros_en_texto, reverificar_hallazgos_sin_ia, texto_pdf,
)

ADMISION = {
    'consecutivo': 33534,
    'identificacion': 'RC 1121978991',
    'paciente': 'JUAN ALEXIS BEJARANO PEÑA',
    'cups': ['881302 - ECOGRAFÍA DE ABDOMEN TOTAL (cant. 1)'],
}
DOC_MAL = ("Número de documento incorrecto: documento 'RC 1121979781', admisión 'RC 1121978991' "
           "(paciente JUAN ALEXIS BEJARANO PEÑA).")
NOMBRE_MAL = "Nombre del usuario incorrecto: documento 'JUAN ALEXIS BEJARAN PEÑA', admisión 'JUAN ALEXIS BEJARANO PEÑA'."
TEXTO_ORDEN = ("ORDEN MEDICA\nPaciente: JUAN ALEXIS BEJARANO PEÑA  RC 1121978991\n"
               "881302 ECOGRAFIA DE ABDOMEN TOTAL  Cantidad 1\nNRO FORMULA 202610230001234567")


def _pdf(paginas):
    """PDF digital con un texto por página ('' = página sin texto, como un escaneo)."""
    from reportlab.pdfgen import canvas
    f = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
    f.close()
    c = canvas.Canvas(f.name)
    for texto in paginas:
        y = 800
        for linea in texto.split('\n'):
            c.drawString(40, y, linea)
            y -= 16
        c.showPage()
    c.save()
    return f.name


class NumerosEnTextoTests(SimpleTestCase):
    def test_quita_separadores_de_miles(self):
        self.assertIn('1121978991', numeros_en_texto('CC 1.121.978.991'))
        self.assertIn('1121978991', numeros_en_texto('CC 1,121,978,991'))

    def test_no_une_numeros_separados_por_espacio(self):
        nums = numeros_en_texto('RC 1121978991 123')
        self.assertIn('1121978991', nums)
        self.assertNotIn('1121978991123', nums)

    def test_numero_pegado_a_letras(self):
        self.assertIn('1121978991', numeros_en_texto('RC1121978991'))


class TextoPdfTests(SimpleTestCase):
    def tearDown(self):
        for ruta in getattr(self, 'rutas', []):
            os.unlink(ruta)

    def _crear(self, paginas):
        ruta = _pdf(paginas)
        self.rutas = getattr(self, 'rutas', []) + [ruta]
        return ruta

    def test_pdf_digital_completo(self):
        texto, completo = texto_pdf(self._crear([TEXTO_ORDEN]))
        self.assertTrue(completo)
        self.assertIn('1121978991', texto)

    def test_pagina_sin_texto_no_es_completo(self):
        _, completo = texto_pdf(self._crear([TEXTO_ORDEN, '']))
        self.assertFalse(completo)

    def test_imagen_o_inexistente(self):
        self.assertEqual(texto_pdf('C:/no/existe.pdf'), ('', False))
        self.assertEqual(texto_pdf('foto.jpg'), ('', False))
        self.assertEqual(texto_pdf(''), ('', False))


class DocumentoIncorrectoTests(SimpleTestCase):
    def test_corregido_en_zeus(self):
        datos = dict(ADMISION, identificacion='RC 1121979781')
        self.assertEqual(evaluar_hallazgo('ORDEN', DOC_MAL, datos)[0], CORREGIDO)

    def test_sin_cambios_en_zeus_y_sin_pdf_sigue_abierto(self):
        self.assertIsNone(evaluar_hallazgo('ORDEN', DOC_MAL, ADMISION))

    def test_ia_leyo_mal_el_pdf_digital(self):
        r = evaluar_hallazgo('ORDEN', DOC_MAL, ADMISION, texto=TEXTO_ORDEN)
        self.assertEqual(r[0], DESCARTADO)
        self.assertIn('1121978991', r[1])

    def test_pdf_tiene_tambien_el_numero_errado(self):
        texto = TEXTO_ORDEN + '\nAcudiente RC 1121979781'
        self.assertIsNone(evaluar_hallazgo('ORDEN', DOC_MAL, ADMISION, texto=texto))

    def test_pdf_sin_el_numero_de_la_admision(self):
        texto = TEXTO_ORDEN.replace('1121978991', '1121979781')
        self.assertIsNone(evaluar_hallazgo('ORDEN', DOC_MAL, ADMISION, texto=texto))


class NombreIncorrectoTests(SimpleTestCase):
    def test_corregido_en_zeus(self):
        datos = dict(ADMISION, paciente='JUAN ALEXIS BEJARAN PEÑA')
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', NOMBRE_MAL, datos)[0], CORREGIDO)

    def test_ia_leyo_mal_el_nombre(self):
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', NOMBRE_MAL, ADMISION, texto=TEXTO_ORDEN)[0], DESCARTADO)

    def test_el_documento_si_tiene_el_nombre_mal_escrito(self):
        texto = TEXTO_ORDEN + '\nAfiliado: JUAN ALEXIS BEJARAN PEÑA'
        self.assertIsNone(evaluar_hallazgo('AUTORIZACION', NOMBRE_MAL, ADMISION, texto=texto))


class PacienteNoCorrespondeTests(SimpleTestCase):
    OTRO = "Paciente no corresponde: documento 'MARIA LOPEZ GOMEZ', admisión 'JUAN ALEXIS BEJARANO PEÑA'."
    SIN_DATOS = ("Paciente no corresponde: el documento no contiene el nombre ni el documento de identidad del "
                 "paciente de la admisión (JUAN ALEXIS BEJARANO PEÑA, RC 1121978991).")

    def test_ia_leyo_mal_y_el_pdf_tiene_nombre_y_documento(self):
        self.assertEqual(evaluar_hallazgo('RESULTADO', self.OTRO, ADMISION, texto=TEXTO_ORDEN)[0], DESCARTADO)

    def test_pdf_con_la_otra_persona_sigue_abierto(self):
        texto = TEXTO_ORDEN + '\nMARIA LOPEZ GOMEZ'
        self.assertIsNone(evaluar_hallazgo('RESULTADO', self.OTRO, ADMISION, texto=texto))

    def test_solo_el_nombre_no_alcanza(self):
        texto = TEXTO_ORDEN.replace('1121978991', '')
        self.assertIsNone(evaluar_hallazgo('RESULTADO', self.OTRO, ADMISION, texto=texto))

    def test_admision_corregida_al_paciente_del_archivo(self):
        datos = dict(ADMISION, paciente='MARIA LOPEZ GOMEZ', identificacion='CC 52000111')
        self.assertEqual(evaluar_hallazgo('RESULTADO', self.OTRO, datos)[0], CORREGIDO)

    def test_sin_datos_corregido_cuando_el_pdf_ya_coincide(self):
        self.assertEqual(evaluar_hallazgo('RESULTADO', self.SIN_DATOS, ADMISION, texto=TEXTO_ORDEN)[0], CORREGIDO)
        self.assertIsNone(evaluar_hallazgo('RESULTADO', self.SIN_DATOS, ADMISION, texto='OTRO TEXTO ' * 10))


class FirmaComprobanteTests(SimpleTestCase):
    DESC = "Ausencia de firma del paciente en el comprobante."

    def test_firma_detectada(self):
        self.assertEqual(evaluar_hallazgo('COMPROBANTE', self.DESC, ADMISION, firma=True)[0], DESCARTADO)

    def test_firma_no_detectada(self):
        self.assertIsNone(evaluar_hallazgo('COMPROBANTE', self.DESC, ADMISION, firma=False))
        self.assertIsNone(evaluar_hallazgo('COMPROBANTE', self.DESC, ADMISION, firma=None))

    def test_otro_motivo_ademas_de_la_firma(self):
        desc = "Ausencia de firma y nombre del paciente incorrecto."
        self.assertIsNone(evaluar_hallazgo('COMPROBANTE', desc, ADMISION, firma=True))

    def test_solo_comprobantes(self):
        self.assertIsNone(evaluar_hallazgo('ORDEN', self.DESC, ADMISION, firma=True))


class ProcedimientoTests(SimpleTestCase):
    CONSULTA = dict(ADMISION, cups=['890274 - CONSULTA DE PRIMERA VEZ POR ESPECIALISTA EN NEUROLOGÍA (cant. 1)'])
    HC = ("HISTORIA CLINICA - CONTROL NEUROLOGIA\nMOTIVO DE CONSULTA: control\n"
          "Antecedentes: POLISOMNOGRAFIA 2025 con apnea moderada")
    DESC = "El estudio del documento (polisomnografía) no corresponde al procedimiento de la admisión."

    def test_resultado_consulta_documentada(self):
        self.assertEqual(evaluar_hallazgo('RESULTADO', self.DESC, self.CONSULTA, texto=self.HC)[0], DESCARTADO)

    def test_mismo_codigo_cups(self):
        desc = "No se especifica el procedimiento en la orden."
        self.assertEqual(evaluar_hallazgo('ORDEN', desc, ADMISION, texto=TEXTO_ORDEN)[0], DESCARTADO)

    def test_lateralidad_no_se_descarta(self):
        desc = "El procedimiento de la orden es de rodilla derecha y la admisión izquierda."
        self.assertIsNone(evaluar_hallazgo('ORDEN', desc, ADMISION, texto=TEXTO_ORDEN))
        self.assertFalse(necesita_pdf('ORDEN', desc))

    def test_sin_texto_no_decide(self):
        self.assertIsNone(evaluar_hallazgo('RESULTADO', self.DESC, self.CONSULTA))

    def test_otro_codigo_cups(self):
        desc = "No se especifica el procedimiento en la orden."
        texto = TEXTO_ORDEN.replace('881302', '883101')
        self.assertIsNone(evaluar_hallazgo('ORDEN', desc, ADMISION, texto=texto))


class HallazgoAceptadoTests(SimpleTestCase):
    def test_aceptado_conserva_lo_que_leyo_la_ia(self):
        # 33391: orden con 'MAIRA JIMENEZ' aceptada a mano; el documento sigue teniendo ese nombre
        adm = dict(ADMISION, paciente='YOJEIDIS MARIA JIMENEZ MARTINEZ')
        r = SimpleNamespace(TipoHallazgo='INFORMACION', Resuelta=True, Resolucion='ACEPTADO', NotaResolucion='x',
                            Descripcion="Nombre del usuario incorrecto: documento 'MAIRA JIMENEZ', "
                                        "admisión 'YOJEIDIS MARIA JIMENEZ MARTINEZ'.")
        self.assertEqual(reverificacion._lectura_de_revision(r, adm)['nombre'], 'MAIRA JIMENEZ')


class CantidadAnotadaTests(SimpleTestCase):
    """Caso 33486: tabla con cantidad 1, pero al pie de cada autorización 'CANTIDAD 2' (Zeus: 2 y 2)."""
    DESC = ("Cantidad autorizada para RADIOGRAFÍA DE CADERA es 1, se esperaba 2; para RADIOGRAFÍA DE PIE es 1, "
            "se esperaba 2.")
    PAGINA = ("AUTORIZACION DE SERVICIOS\nServicios Autorizados Cantidad\n{cups} 1\n"
              "NO REQUIERE PAGO\nVALIDO HASTA 20261214\nCANTIDAD 2\n")
    TEXTO = PAGINA.format(cups='873411 RADIOGRAFIA DE CADERA') + PAGINA.format(cups='873333 RADIOGRAFIA DE PIE')
    ADM = dict(ADMISION, cups=['873411 - RADIOGRAFÍA DE CADERA (cant. 2)', '873333 - RADIOGRAFÍA DE PIE (cant. 2)'])

    def test_anotacion_de_cantidad_en_cada_pagina(self):
        self.assertTrue(necesita_pdf('AUTORIZACION', self.DESC))
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', self.DESC, self.ADM, texto=self.TEXTO)[0], DESCARTADO)

    def test_anotacion_solo_en_una_pagina_se_mantiene(self):
        texto = self.PAGINA.format(cups='873411') + "AUTORIZACION 873333 RADIOGRAFIA DE PIE 1\n"
        self.assertIsNone(evaluar_hallazgo('AUTORIZACION', self.DESC, self.ADM, texto=texto))

    def test_sin_texto_o_con_otro_motivo_se_mantiene(self):
        self.assertIsNone(evaluar_hallazgo('AUTORIZACION', self.DESC, self.ADM))
        desc = self.DESC + " Autorización vencida."
        self.assertIsNone(evaluar_hallazgo('AUTORIZACION', desc, self.ADM, texto=self.TEXTO))


class ParticularTests(SimpleTestCase):
    """Caso 32999: particular; la orden es consulta de ortopedia (890380) 'CON RX DE RODILLAS', se factura la RX."""
    ADM = dict(ADMISION, entidad='PARTICULAR TARIFA PLENA', contrato='PARTICULAR TARIFA PLENA',
               cups=['873420 - RADIOGRAFÍA DE RODILLA (AP- LATERAL) (cant. 2)'])
    DESC = "Procedimiento en documento '890380' no coincide con '873420' de admisión. Se esperaba RX de rodilla."

    def test_orden_de_particular_con_otro_procedimiento(self):
        self.assertEqual(evaluar_hallazgo('ORDEN', self.DESC, self.ADM)[0], DESCARTADO)
        adm = dict(self.ADM, entidad='', contrato='PARTICULAR 10% DESCUENTO')
        self.assertEqual(evaluar_hallazgo('ORDEN', self.DESC, adm)[0], DESCARTADO)

    def test_eps_u_otro_motivo_se_mantiene(self):
        eps = dict(self.ADM, entidad='PARTICULAR 10% DESCUENTO', contrato='CAPITAL SALUD SUBSIDIADO')
        self.assertIsNone(evaluar_hallazgo('ORDEN', self.DESC, eps))
        # Particular: ningún hallazgo se mantiene (orden vencida, resultado...), no se validan sus documentos
        self.assertEqual(evaluar_hallazgo('ORDEN', self.DESC + " Orden vencida.", self.ADM)[0], DESCARTADO)
        self.assertEqual(evaluar_hallazgo('RESULTADO', self.DESC, self.ADM)[0], DESCARTADO)


class CostoIATests(SimpleTestCase):
    """Ahorro de llamadas e imágenes a OpenAI."""

    def tearDown(self):
        for ruta in getattr(self, 'rutas', []):
            os.unlink(ruta)

    def _crear(self, paginas):
        ruta = _pdf(paginas)
        self.rutas = getattr(self, 'rutas', []) + [ruta]
        return ruta

    def test_paginas_en_imagen_segun_tipo(self):
        from gedocumental.services.ai_revision import _leer_documento
        ruta = self._crear(['', '', ''])   # escaneado de 3 páginas
        self.assertEqual(len(_leer_documento(ruta, 'HCLINICA')[1]), 1)
        self.assertEqual(len(_leer_documento(ruta, 'RESULTADO')[1]), 2)
        self.assertEqual(len(_leer_documento(ruta, 'AUTORIZACION')[1]), 3)

    def test_comprobante_digital_con_firma_detectada_va_sin_imagen(self):
        from gedocumental.services.ai_revision import _leer_documento
        ruta = self._crear([TEXTO_ORDEN])
        self.assertEqual(len(_leer_documento(ruta, 'COMPROBANTE', ver_firma=False)[1]), 0)
        self.assertEqual(len(_leer_documento(ruta, 'COMPROBANTE')[1]), 1)

    def _llamar(self, descripcion):
        import json
        from django.test import override_settings
        from gedocumental.services.ai_revision import _llamar_openai
        respuesta = {
            "paciente_documento": "BEJARANO PEÑA JUAN ALEXIS", "nombre_documento": "BEJARANO PEÑA JUAN ALEXIS",
            "identificacion_documento": "1121978991", "paciente_coincide": "NO",
            "procedimientos_documento": "881302", "procedimientos_coinciden": "SI",
            "firma_paciente": "NO_APLICA", "tipo": "INFORMACION", "descripcion": descripcion,
        }
        client = mock.MagicMock()
        client.chat.completions.create.return_value = mock.MagicMock(
            choices=[mock.MagicMock(message=mock.MagicMock(content=json.dumps(respuesta)))],
            usage=mock.MagicMock(prompt_tokens=25000, completion_tokens=300))
        with override_settings(OPENAI_API_KEY='x'), mock.patch('openai.OpenAI', return_value=client):
            r = _llamar_openai('', ['img'], 'ORDEN', ADMISION)
        return r, client.chat.completions.create.call_count

    def test_correccion_del_sistema_sin_segunda_llamada(self):
        r, llamadas = self._llamar("Paciente no corresponde: documento 'BEJARANO PEÑA JUAN ALEXIS', "
                                   "admisión 'JUAN ALEXIS BEJARANO PEÑA'.")
        self.assertEqual((r['tipo'], llamadas, r['llamadas'], r['tokens']), ('OK', 1, 1, [25000, 300]))

    def test_con_otro_motivo_repite_la_consulta(self):
        r, llamadas = self._llamar("Paciente no corresponde: documento 'BEJARANO PEÑA JUAN ALEXIS'. "
                                   "Orden vencida.")
        self.assertEqual((llamadas, r['llamadas'], r['tokens']), (2, 2, [50000, 600]))


class TranscripcionTablaTests(SimpleTestCase):
    """Caso 31807: orden escaneada con RM, EEG y, en la última fila, la consulta de neurología pediátrica."""
    ADM = dict(ADMISION, cups=['890375 - CONSULTA DE CONTROL POR NEUROLOGIA PEDIATRICA (cant. 1)'])
    DESC = ("Procedimientos en documento: RM y EEG; se esperaba consulta de control por especialista en "
            "neurología pediátrica.")

    def _llamar(self, transcripcion, tipo='ORDEN', imagenes=('img',)):
        import json
        from django.test import override_settings
        from gedocumental.services.ai_revision import _llamar_openai
        revision = {
            "nombre_documento": "AREVALO ACUÑA NICOLE", "identificacion_documento": "1121902635",
            "paciente_coincide": "SI", "procedimientos_documento": "883101 RM; 891402 EEG",
            "procedimientos_coinciden": "NO", "firma_paciente": "NO_APLICA", "tipo": "SOPORTES",
            "descripcion": self.DESC,
            "estudio_documento": {"modalidad": "RM", "region": "CABEZA"},
            "estudio_admision": {"modalidad": "CONSULTA", "especialidad": "NEUROLOGIA_PEDIATRICA"},
        }

        def respuesta(contenido, tokens):
            return mock.MagicMock(choices=[mock.MagicMock(message=mock.MagicMock(content=json.dumps(contenido)))],
                                  usage=mock.MagicMock(prompt_tokens=tokens, completion_tokens=100))
        client = mock.MagicMock()
        client.chat.completions.create.side_effect = [
            respuesta(revision, 25000), respuesta({"procedimientos": transcripcion}, 20000)]
        with override_settings(OPENAI_API_KEY='x'), mock.patch('openai.OpenAI', return_value=client):
            r = _llamar_openai('', list(imagenes), tipo, self.ADM)
        return r, client.chat.completions.create.call_count

    def test_la_transcripcion_trae_la_ultima_fila(self):
        r, llamadas = self._llamar("883101 RESONANCIA MAGNETICA DE CEREBRO; 891402 ELECTROENCEFALOGRAMA; "
                                   "890375 CONSULTA DE CONTROL POR NEUROLOGIA PEDIATRICA")
        self.assertEqual((r['tipo'], llamadas, r['llamadas'], r['tokens']), ('OK', 2, 2, [45000, 200]))
        self.assertTrue(r['ajuste_transcripcion'])

    def test_la_transcripcion_tampoco_la_trae(self):
        r, llamadas = self._llamar("883101 RESONANCIA MAGNETICA DE CEREBRO; 891402 ELECTROENCEFALOGRAMA")
        self.assertEqual((r['tipo'], llamadas), ('SOPORTES', 2))

    def test_documento_digital_no_hace_la_consulta_extra(self):
        r, llamadas = self._llamar("890375", imagenes=())
        self.assertEqual((r['tipo'], llamadas), ('SOPORTES', 1))


class RelecturaCantidadTests(SimpleTestCase):
    """Caso 35377: autorización escaneada con 'Cantidad 1' en la tabla y 'CANTIDAD 8' al pie; Zeus tiene 8."""
    ADM = dict(ADMISION, paciente='WILLIAM ALEXANDER PARAMO HIGUERA', identificacion='CC 1003777397',
               cups=['891509 - NEUROCONDUCCIÓN (CADA NERVIO) (cant. 8)'])

    def _llamar(self, relectura):
        import json
        from django.test import override_settings
        from gedocumental.services.ai_revision import _llamar_openai
        revision = {
            "nombre_documento": "WILLIAM ALEXANDER PARAMO HIGUERA", "identificacion_documento": "1003777397",
            "paciente_coincide": "SI", "procedimientos_coinciden": "SI", "firma_paciente": "NO_APLICA",
            "tipo": "AUTORIZACION", "descripcion": "Cantidad autorizada incorrecta: documento '1', admisión '8'.",
            "cantidad_anotada": "",
        }
        def respuesta(contenido):
            return mock.MagicMock(choices=[mock.MagicMock(message=mock.MagicMock(content=json.dumps(contenido)))],
                                  usage=mock.MagicMock(prompt_tokens=100, completion_tokens=10))
        client = mock.MagicMock()
        client.chat.completions.create.side_effect = [respuesta(revision), respuesta(relectura)]
        with override_settings(OPENAI_API_KEY='x'), mock.patch('openai.OpenAI', return_value=client):
            return _llamar_openai('', ['img'], 'AUTORIZACION', self.ADM)

    def test_anotacion_igual_a_zeus_no_es_hallazgo(self):
        self.assertEqual(self._llamar({"cantidad_anotada": "8"})['tipo'], 'OK')

    def test_sin_anotacion_sigue_el_hallazgo(self):
        self.assertEqual(self._llamar({"cantidad_anotada": ""})['tipo'], 'AUTORIZACION')
        self.assertEqual(self._llamar({"cantidad_anotada": "3"})['tipo'], 'AUTORIZACION')


class ContrasteConAfirmacionesTests(SimpleTestCase):
    """Caso 35656: 'Nombre del usuario correcto; procedimiento coincide. Contraste en admisión es SIN y en documento
    es CON.' El CUPS 883440 (RM de pelvis) sirve para simple y contrastada; la afirmación del nombre no es motivo."""
    DESC = ("Nombre del usuario correcto; procedimiento coincide. Contraste en admisión es SIN y en documento "
            "es CON.")

    def test_solo_contraste_con_afirmaciones(self):
        from gedocumental.services.ai_revision import _solo_motivo_contraste
        adm = {'cups': ['883440 - RESONANCIA MAGNETICA DE PELVIS (cant. 1)']}
        self.assertTrue(_solo_motivo_contraste(self.DESC, adm))
        adm_simple = {'cups': ['883440 - RESONANCIA MAGNETICA DE PELVIS SIMPLE (cant. 1)']}
        self.assertFalse(_solo_motivo_contraste(self.DESC, adm_simple))

    def test_admision_con_insumos_de_contraste(self):
        # Admisión real de la 35656: el CUPS no dice contraste, pero lleva gadolinio y la jeringa del inyector
        from gedocumental.services.ai_revision import _solo_motivo_contraste
        adm = {'cups': ['883440 - RESONANCIA MAGNÉTICA DE PELVIS (cant. 1)',
                        'None - VENOCAT IV NO. 20 ROSADO (cant. 1)',
                        'None - CLARISCAN 0.5MMOL (ACIDO GADOTERICO) (cant. 1)',
                        'None - JERINGA INYECTOR DE CONTRASTE SINO MEDICAL -RESONADOR (cant. 1)',
                        'None - (Revertido) -  CLARISCAN 0.5MMOL (ACIDO GADOTERICO) (cant. 1)']}
        self.assertTrue(_solo_motivo_contraste(self.DESC, adm))
        sin_insumos = {'cups': ['883440 - RESONANCIA MAGNÉTICA DE PELVIS (cant. 1)',
                                'None - JERINGA 10 ML (cant. 1)']}
        self.assertTrue(_solo_motivo_contraste(self.DESC, sin_insumos))

    def test_nombre_incorrecto_sigue_siendo_otro_motivo(self):
        from gedocumental.services.ai_revision import _solo_motivo_contraste
        adm = {'cups': ['883440 - RESONANCIA MAGNETICA DE PELVIS (cant. 1)']}
        self.assertFalse(_solo_motivo_contraste("Nombre del usuario incorrecto. Contraste distinto.", adm))

    def test_reverificacion_descarta(self):
        adm = dict(ADMISION, paciente='LEIDY CAROLINA LOPEZ COCUY', identificacion='CC 40305485',
                   cups=['883440 - RESONANCIA MAGNETICA DE PELVIS (cant. 1)'])
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', self.DESC, adm)[0], DESCARTADO)


class NombreDelEncabezadoTests(SimpleTestCase):
    """Caso 35608: la IA tomó al médico del encabezado (CAMILO HERNANDEZ RUBIO, NIT 16623180) como paciente."""
    ADM = dict(ADMISION, paciente='CAMILO CABRERA JARAMILLO', identificacion='CC 17312171')

    def _llamar(self, relectura):
        import json
        from django.test import override_settings
        from gedocumental.services.ai_revision import _llamar_openai
        revision = {
            "nombre_documento": "CAMILO HERNANDEZ RUBIO", "identificacion_documento": "16623180",
            "paciente_coincide": "NO", "procedimientos_coinciden": "SI", "firma_paciente": "NO_APLICA",
            "tipo": "INFORMACION", "descripcion": "Paciente no corresponde.",
        }
        def respuesta(contenido):
            return mock.MagicMock(choices=[mock.MagicMock(message=mock.MagicMock(content=json.dumps(contenido)))],
                                  usage=mock.MagicMock(prompt_tokens=100, completion_tokens=10))
        client = mock.MagicMock()
        client.chat.completions.create.side_effect = [respuesta(revision), respuesta(relectura)]
        with override_settings(OPENAI_API_KEY='x'), mock.patch('openai.OpenAI', return_value=client):
            return _llamar_openai('', ['img'], 'ORDEN', self.ADM)

    def test_relectura_con_el_paciente_no_es_hallazgo(self):
        r = self._llamar({"nombre": "CABRERA JARAMILLO CAMILO"})
        self.assertEqual(r['tipo'], 'OK')
        self.assertEqual(r['paciente_coincide'], 'SI')

    def test_relectura_con_otra_persona_sigue(self):
        r = self._llamar({"nombre": "CAMILO HERNANDEZ RUBIO"})
        self.assertTrue(r['descripcion'].startswith('Paciente no corresponde: documento'))


class CantidadPorCupsTests(SimpleTestCase):
    """Caso 33030: dos autorizaciones en un PDF ('CANTIDAD 2' para 930860, 'CANTIDAD 8' para 891509)."""
    ADM = dict(ADMISION, paciente='ARNOLDO ROJAS', identificacion='CC 17313342', cups=[
        '891509 - NEUROCONDUCCIÓN (CADA NERVIO) 8 NERVIOS (cant. 1)',
        '930860 - ELECTROMIOGRAFÍA EN CADA EXTREMIDAD (UNO O MÁS MÚSCULOS) (cant. 2)'])

    def _llamar(self, cantidades, tipo='AUTORIZACION', adm=None):
        import json
        from django.test import override_settings
        from gedocumental.services.ai_revision import _llamar_openai
        revision = {
            "nombre_documento": "ARNOLDO ROJAS", "identificacion_documento": "17313342",
            "paciente_coincide": "SI", "procedimientos_coinciden": "SI", "firma_paciente": "NO_APLICA",
            "tipo": "OK", "descripcion": "Documento correcto", "cantidades_documento": cantidades,
        }
        client = mock.MagicMock()
        client.chat.completions.create.return_value = mock.MagicMock(
            choices=[mock.MagicMock(message=mock.MagicMock(content=json.dumps(revision)))],
            usage=mock.MagicMock(prompt_tokens=1000, completion_tokens=100))
        with override_settings(OPENAI_API_KEY='x'), mock.patch('openai.OpenAI', return_value=client):
            return _llamar_openai('', ['img'], tipo, adm or self.ADM)

    def test_cantidad_anotada_distinta_a_zeus(self):
        r = self._llamar('930860:2; 891509:8')
        self.assertEqual(r['tipo'], 'AUTORIZACION')
        self.assertEqual(r['descripcion'], 'Cantidad autorizada en el documento es 8, se esperaba 1 para el '
                                           'procedimiento 891509.')
        # Se corrige en Zeus: la reverificación lo cierra; si no, sigue abierto
        corregida = dict(self.ADM, cups=[self.ADM['cups'][0].replace('(cant. 1)', '(cant. 8)'), self.ADM['cups'][1]])
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', r['descripcion'], corregida)[0], CORREGIDO)
        self.assertIsNone(evaluar_hallazgo('AUTORIZACION', r['descripcion'], self.ADM))

    def test_cantidades_iguales_u_otro_documento(self):
        self.assertEqual(self._llamar('930860:2; 891509-8:1')['tipo'], 'OK')
        self.assertEqual(self._llamar('')['tipo'], 'OK')
        self.assertEqual(self._llamar('930860:2; 891509:8', tipo='ORDEN')['tipo'], 'OK')

    def test_anotacion_igual_a_zeus_prevalece_sobre_la_tabla(self):
        # Caso 35215: tabla 1, "CANTIDAD 2" al pie, Zeus 2; la IA puso la de la tabla en cantidades_documento
        from gedocumental.services.ai_revision import _cantidades_distintas_zeus
        adm = dict(ADMISION, cups=['954626 - POTENCIALES EVOCADOS AUDITIVOS (cant. 2)'])
        self.assertEqual(_cantidades_distintas_zeus('954626:1', adm, 'CANTIDAD 2'), [])
        self.assertEqual(_cantidades_distintas_zeus('954626:1', adm, ''), [('954626', 1, 2)])
        # 33030: la anotación (8) no es la de Zeus (1): sigue siendo hallazgo
        self.assertEqual(_cantidades_distintas_zeus('930860:2; 891509:8', self.ADM, '8'), [('891509', 8, 1)])

    def test_mismo_cups_en_dos_autorizaciones_se_suma(self):
        from gedocumental.services.ai_revision import _cantidades_distintas_zeus
        self.assertEqual(_cantidades_distintas_zeus('930860:1; 930860:1', self.ADM), [])
        self.assertEqual(_cantidades_distintas_zeus('930860:1', self.ADM), [('930860', 1, 2)])


class RelecturaNombreTests(SimpleTestCase):
    """Caso 35228: escaneado 'RIVEROS URREA CARLOS ISAIAS'; la IA leyó 'RIVERA CARLOS ISAIAS'."""
    ADM = dict(ADMISION, paciente='CARLOS ISAIAS RIVEROS URREA', identificacion='CC 86080536')

    def _llamar(self, relectura, imagenes=('img',)):
        import json
        from django.test import override_settings
        from gedocumental.services.ai_revision import _llamar_openai
        revision = {"nombre_documento": "RIVERA CARLOS ISAIAS", "identificacion_documento": "86080536",
                    "paciente_coincide": "SI", "procedimientos_coinciden": "SI", "firma_paciente": "NO_APLICA",
                    "tipo": "OK", "descripcion": "Documento correcto"}

        def respuesta(contenido):
            return mock.MagicMock(choices=[mock.MagicMock(message=mock.MagicMock(content=json.dumps(contenido)))],
                                  usage=mock.MagicMock(prompt_tokens=1000, completion_tokens=50))
        client = mock.MagicMock()
        client.chat.completions.create.side_effect = [respuesta(revision), respuesta({"nombre": relectura})]
        with override_settings(OPENAI_API_KEY='x'), mock.patch('openai.OpenAI', return_value=client):
            return _llamar_openai('', list(imagenes), 'ORDEN', self.ADM), client.chat.completions.create.call_count

    def test_relectura_con_el_nombre_de_zeus(self):
        r, llamadas = self._llamar("RIVEROS URREA CARLOS ISAIAS")
        self.assertEqual((r['tipo'], llamadas, r['llamadas']), ('OK', 2, 2))
        self.assertTrue(r['ajuste_relectura_nombre'])

    def test_relectura_confirma_el_error(self):
        r, llamadas = self._llamar("RIVERA CARLOS ISAIAS")
        self.assertEqual((r['tipo'], llamadas), ('INFORMACION', 2))
        self.assertIn('Nombre del usuario incorrecto', r['descripcion'])

    def test_pdf_digital_no_relee(self):
        r, llamadas = self._llamar("RIVEROS URREA CARLOS ISAIAS", imagenes=())
        self.assertEqual(llamadas, 1)


class ProcedimientoCambiadoEnZeusTests(SimpleTestCase):
    """Caso 34416: la admisión pasó de RM de cerebro a RM de columna cervical (883210) en SIESA."""
    NUEVO = dict(ADMISION, cups=['883210 - RESONANCIA MAGNÉTICA DE COLUMNA CERVICAL SIMPLE (cant. 1)'])
    VIEJO = dict(ADMISION, cups=['883101 - RESONANCIA MAGNÉTICA DE CEREBRO (cant. 1)'])
    AUT = ("Procedimiento no coincide: documento 'resonancia magnÉtica de columna cervical simple', "
           "admisión 'resonancia magnética de cerebro'.")
    ORD = ("Procedimientos no coinciden: documento incluye 'CONSULTA DE CONTROL' y 'RESONANCIA CERVICAL', "
           "admisión esperaba 'RESONANCIA MAGNÉTICA DE CEREBRO'.")

    def test_detecta_el_cambio(self):
        from gedocumental.services.reverificacion import procedimiento_cambio_en_zeus as cambio
        self.assertTrue(cambio(self.AUT, self.NUEVO))
        self.assertTrue(cambio(self.ORD, self.NUEVO))
        self.assertFalse(cambio(self.AUT, self.VIEJO))
        self.assertFalse(cambio(self.ORD, self.VIEJO))
        # Con otro motivo (paciente, vencida...) no se toca
        self.assertFalse(cambio("Autorización vencida; admisión 'resonancia magnética de cerebro'.", self.NUEVO))


class NombreRepetidoTests(SimpleTestCase):
    """Caso 35159: paciente con un solo nombre; la orden lo repite como segundo nombre."""
    ADM = dict(ADMISION, paciente='MARIANGEL GARZON SANCHEZ', identificacion='CC 1118206724')

    def test_nombre_repetido_es_la_misma_persona(self):
        from gedocumental.services.ai_revision import _mismo_paciente
        doc = {'nombre_documento': 'MARIANGEL MARIANGEL GARZON SANCHEZ', 'identificacion_documento': '1118206724'}
        self.assertTrue(_mismo_paciente(doc, self.ADM))
        desc = ("Nombre del usuario incorrecto: documento 'MARIANGEL MARIANGEL GARZON SANCHEZ', "
                "admisión 'MARIANGEL GARZON SANCHEZ'.")
        self.assertEqual(evaluar_hallazgo('ORDEN', desc, self.ADM)[0], CORREGIDO)
        # Otro nombre distinto sigue siendo hallazgo
        self.assertFalse(_mismo_paciente(dict(doc, nombre_documento='MARIANGEL LUCIA GARZON SANCHEZ'), self.ADM))


class NombreAbreviadoTests(SimpleTestCase):
    """Caso 35838: la IA tomó la firma 'Ann G Carrizo'; el campo impreso dice ANNY GICED CARRIZO CHOURIO."""
    ADM = dict(ADMISION, paciente='ANNY GICED CARRIZO CHOURIO', identificacion='PT 5963952')

    def test_nombre_abreviado_es_la_misma_persona(self):
        from gedocumental.services.ai_revision import _mismo_paciente
        self.assertTrue(_mismo_paciente({'nombre_documento': 'Ann G Carrizo', 'identificacion_documento': ''},
                                        self.ADM))
        desc = "Nombre del usuario incorrecto: documento 'Ann G Carrizo', admisión 'ANNY GICED CARRIZO CHOURIO'."
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', desc, self.ADM)[0], CORREGIDO)

    def test_nombre_completo_mal_escrito_sigue_siendo_hallazgo(self):
        from gedocumental.services.ai_revision import _mismo_paciente
        for nombre in ('ANN GICED CARRIZO CHOURIO', 'ANNY GISED CARRIZO CHOURIO', 'ANA CARRIZO'):
            self.assertFalse(_mismo_paciente({'nombre_documento': nombre, 'identificacion_documento': ''},
                                             self.ADM), nombre)


class CupsIgualesZeusTests(SimpleTestCase):
    """Caso 34448: la IA dice que no coinciden, pero repite los mismos CUPS y cantidades de Zeus."""
    ADM = dict(ADMISION, paciente='ANGELA PATRICIA GOMEZ', identificacion='CC 39675670', cups=[
        '871040 - RADIOGRAFÍA DE COLUMNA LUMBOSACRA (cant. 1)',
        '871070 - RADIOGRAFÍA DINÁMICA DE COLUMNA VERTEBRAL (cant. 2)'])
    DESC = ("Procedimientos no coinciden: documento tiene 871070 y 871040, admisión tiene 871040 y 871070. "
            "Cantidad anotada en documento es 2 para 871070, pero solo 1 para 871040.")

    def test_mismos_cups_y_cantidades_se_descarta(self):
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', self.DESC, self.ADM)[0], DESCARTADO)

    def test_diferencia_real_sigue(self):
        from gedocumental.services.ai_revision import _cups_y_cantidades_como_zeus
        self.assertFalse(_cups_y_cantidades_como_zeus(self.DESC.replace('es 2 para', 'es 3 para'), self.ADM))
        self.assertFalse(_cups_y_cantidades_como_zeus(
            "Procedimientos no coinciden: documento tiene 871070, admisión tiene 871040 y 871070.", self.ADM))
        self.assertFalse(_cups_y_cantidades_como_zeus(
            "Cantidad autorizada en el documento es 1, se esperaba 2 para el procedimiento 871070.", self.ADM))
        self.assertFalse(_cups_y_cantidades_como_zeus(self.DESC + " Autorización vencida.", self.ADM))


class CantidadFamiliaCupsTests(SimpleTestCase):
    """Caso 34348: autorización 873420 x2; la admisión factura 873420 x1 + 873422 (comparativas) x1."""
    ADM = dict(ADMISION, paciente='BLANCA ODILIA MORA DE NOVOA', identificacion='CC 21233598', cups=[
        '873420 - RADIOGRAFÍA DE RODILLA (AP- LATERAL) (cant. 1)',
        '873422 - RADIOGRAFÍA DE RODILLAS COMPARATIVAS POSICION VERTICAL (cant. 1)'])

    def test_familia_suma_la_cantidad(self):
        from gedocumental.services.ai_revision import _cantidades_distintas_zeus
        self.assertEqual(_cantidades_distintas_zeus('873420:2', self.ADM), [])
        self.assertEqual(_cantidades_distintas_zeus('873420:3', self.ADM), [('873420', 3, 1)])
        # Si la autorización trae los dos CUPS, cada uno se compara con el suyo
        self.assertEqual(_cantidades_distintas_zeus('873420:2; 873422:1', self.ADM), [('873420', 2, 1)])

    def test_hallazgo_existente_se_cierra(self):
        desc = "Cantidad autorizada en el documento es 2, se esperaba 1 para el procedimiento 873420."
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', desc, self.ADM)[0], CORREGIDO)
        otra = dict(self.ADM, cups=['873420 - RADIOGRAFÍA DE RODILLA (AP- LATERAL) (cant. 1)',
                                    '873111 - RADIOGRAFÍA DE CRÁNEO (cant. 1)'])
        self.assertIsNone(evaluar_hallazgo('AUTORIZACION', desc, otra))


class TerceroConApellidoDeMasTests(SimpleTestCase):
    """Caso 33686: la orden trae un apellido de más; la autorización de la EPS dice lo mismo que Zeus."""
    ADM = dict(ADMISION, paciente='MARIA ALEJANDRA FERNANDEZ DE CASTRO', identificacion='RC 1121981943')
    DESC = ("Nombre del usuario incorrecto: documento 'BEJARANO FERDANDEZ DE CASTRO MARIA ALEJANDRA', "
            "admisión 'MARIA ALEJANDRA FERNANDEZ DE CASTRO'.")
    AUT = {'nombre': 'MARIA ALEJANDRA FERNANDEZ DE CASTRO', 'documento': '1121981943'}

    def test_apellido_de_mas_es_siesa_incompleto(self):
        # La orden (y la autorización de Salud Total) traen BEJARANO: a SIESA le falta un apellido
        self.assertIsNone(evaluar_hallazgo('ORDEN', self.DESC, self.ADM, autorizacion=self.AUT))
        otras = [{'tipo': 'AUTORIZACION', 'documento': None, 'nombre': self.AUT['nombre']}]
        self.assertIsNone(evaluar_hallazgo('ORDEN', self.DESC, self.ADM, autorizacion=self.AUT, otras=otras))

    def test_sin_autorizacion_o_autorizacion_distinta_sigue(self):
        self.assertIsNone(evaluar_hallazgo('ORDEN', self.DESC, self.ADM))
        otra = {'nombre': 'MARIA ALEJANDRA BEJARANO FERNANDEZ DE CASTRO', 'documento': '1121981943'}
        self.assertIsNone(evaluar_hallazgo('ORDEN', self.DESC, self.ADM, autorizacion=otra))
        # En la autorización (documento de la EPS) sí es hallazgo
        self.assertIsNone(evaluar_hallazgo('AUTORIZACION', self.DESC, self.ADM, autorizacion=self.AUT))
        # Un nombre distinto con más palabras no se descarta
        desc = self.DESC.replace('MARIA ALEJANDRA\'', 'LUISA FERNANDA\'')
        self.assertIsNone(evaluar_hallazgo('ORDEN', desc, self.ADM, autorizacion=self.AUT))


class NombreConfirmadoPorOtroDocumentoTests(SimpleTestCase):
    """Si la orden o la autorización tiene el nombre de SIESA, el nombre mal escrito en otro documento no es hallazgo."""
    ADM = dict(ADMISION, paciente='MARIA ALEJANDRA FERNANDEZ DE CASTRO', identificacion='RC 1121981943')
    DESC = ("Nombre del usuario incorrecto: documento 'MARIA ALEJANDRA FERNANDES CASTRO', "
            "admisión 'MARIA ALEJANDRA FERNANDEZ DE CASTRO'.")
    IGUAL = {'nombre': 'MARIA ALEJANDRA FERNANDEZ DE CASTRO', 'documento': '1121981943'}
    MAL = {'nombre': 'MARIA ALEJANDRA FERNANDES CASTRO', 'documento': '1121981943'}

    def test_orden_correcta_autorizacion_mal(self):
        otras = [dict(self.IGUAL, tipo='ORDEN')]
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', self.DESC, self.ADM, otras=otras)[0], DESCARTADO)

    def test_autorizacion_correcta_orden_mal(self):
        otras = [dict(self.IGUAL, tipo='AUTORIZACION')]
        self.assertEqual(evaluar_hallazgo('ORDEN', self.DESC, self.ADM, otras=otras)[0], DESCARTADO)

    def test_dos_externos_con_el_mismo_error_es_de_siesa(self):
        # La orden escribe el nombre igual que la autorización y distinto de SIESA: el error es de SIESA
        otras = [dict(self.IGUAL, tipo='COMPROBANTE'), dict(self.IGUAL, tipo='RESULTADO'),
                 dict(self.MAL, tipo='ORDEN')]
        self.assertIsNone(evaluar_hallazgo('AUTORIZACION', self.DESC, self.ADM, otras=otras))

    def test_cada_externo_con_un_error_distinto_no_es_hallazgo(self):
        otras = [{'tipo': 'ORDEN', 'documento': None, 'nombre': 'MARIA ALEJANDRA HERNANDEZ DE CASTRO'}]
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', self.DESC, self.ADM, otras=otras)[0], DESCARTADO)

    def test_sin_otro_documento_externo_no_se_puede_comparar(self):
        self.assertIsNone(evaluar_hallazgo('AUTORIZACION', self.DESC, self.ADM, otras=[]))


class ApellidoConApostrofoTests(SimpleTestCase):
    """Caso 33220: FOMAG escribe PACHON D`ACHIARDI; SIESA, PACHON DACHIARDI."""
    ADM = dict(ADMISION, paciente='MARTHA MARLEN PACHON DACHIARDI', identificacion='CC 41721505')

    def test_apostrofo_es_formato(self):
        desc = ("Nombre del usuario incorrecto: documento 'PACHON D' ACHIARDI MARTHA MARLEN', "
                "admisión 'MARTHA MARLEN PACHON DACHIARDI'.")
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', desc.replace("D' ", "D` "), self.ADM)[0], CORREGIDO)
        from gedocumental.services.ai_revision import _mismo_paciente
        for nombre in ("PACHON D`ACHIARDI MARTHA MARLEN", "PACHON D’ ACHIARDI MARTHA MARLEN"):
            self.assertTrue(_mismo_paciente({'nombre_documento': nombre, 'identificacion_documento': ''},
                                            self.ADM), nombre)
        self.assertFalse(_mismo_paciente({'nombre_documento': 'PACHON ACHIARDI MARTHA MARLEN',
                                          'identificacion_documento': ''}, self.ADM))


class AutorizacionSoatTests(SimpleTestCase):
    """Caso 33369: en SOAT la autorización es el radicado de SIRAS (trae el nombre de quien radica)."""
    ADM = dict(ADMISION, paciente='CAMILO ANDRES GOMEZ ORJUELA', identificacion='CC 1121846868',
               entidad='MUNDIAL SOAT', contrato='MUNDIAL SOAT')
    DESC = ("Paciente no corresponde: documento 'NICOLÁS JESÚS PERDOMO VARGAS', "
            "admisión 'CAMILO ANDRES GOMEZ ORJUELA'.")

    def test_autorizacion_soat_se_descarta(self):
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', self.DESC, self.ADM)[0], DESCARTADO)

    def test_otros_documentos_soat_si_se_revisan(self):
        self.assertIsNone(evaluar_hallazgo('ORDEN', self.DESC, self.ADM))

    def test_solo_soat(self):
        from gedocumental.services.ai_revision import _autorizacion_soat
        self.assertTrue(_autorizacion_soat('AUTORIZACION', dict(self.ADM, contrato='', entidad='SEGUROS DEL ESTADO SOAT')))
        self.assertFalse(_autorizacion_soat('AUTORIZACION', dict(self.ADM, contrato='SANITAS', entidad='SANITAS EPS')))

    def test_no_llama_a_la_ia(self):
        from gedocumental.services import ai_revision
        archivo = mock.MagicMock(Tipo='AUTORIZACION')
        with mock.patch.object(ai_revision, '_registrar_revision') as registrar,                 mock.patch.object(ai_revision, '_llamar_openai') as llamar:
            r = ai_revision.revisar_archivo(archivo, self.ADM)
        self.assertEqual(r['tipo'], 'OK')
        llamar.assert_not_called()
        registrar.assert_called_once()


class AdicionalesNoSeRevisanTests(SimpleTestCase):
    """Caso 35274: los soportes ADICIONALES no se revisan; el nombre del servicio pegado al del paciente."""
    ADM = dict(ADMISION, paciente='MIREYA MERCHAN REYES', identificacion='CC 40417054',
               cups=['890264 - CONSULTA DE PRIMERA VEZ POR ESPECIALISTA EN MEDICINA FISICA Y REHABILITACION'])
    DESC = ("Paciente no corresponde: documento 'MIREYA FISICA Y REHABILITACION 1A VEZ CONSUL', "
            "admisión 'MIREYA MERCHAN REYES'.")

    def test_adicionales_no_llaman_a_la_ia(self):
        from gedocumental.services.ai_revision import TIPOS_SIN_LLAMADA_IA
        self.assertIn('ADICIONALES', TIPOS_SIN_LLAMADA_IA)

    def test_hallazgo_de_adicionales_se_descarta(self):
        self.assertEqual(evaluar_hallazgo('ADICIONALES', self.DESC, self.ADM)[0], DESCARTADO)

    def test_nombre_del_servicio_pegado_no_es_otro_paciente(self):
        from gedocumental.services.ai_revision import _mismo_paciente
        doc = {'nombre_documento': 'MIREYA FISICA Y REHABILITACION 1A VEZ CONSUL', 'identificacion_documento': ''}
        self.assertTrue(_mismo_paciente(doc, self.ADM))
        # Otro nombre de pila con el servicio pegado sigue siendo otra persona
        self.assertFalse(_mismo_paciente(dict(doc, nombre_documento='LUCIA FISICA Y REHABILITACION 1A VEZ'), self.ADM))
        # Sin servicio pegado, un solo nombre no basta
        self.assertFalse(_mismo_paciente(dict(doc, nombre_documento='MIREYA'), self.ADM))


class MismoPacienteSoportesTests(SimpleTestCase):
    """Caso 34505: la IA marca SOPORTES 'Paciente no corresponde' con el mismo nombre en documento y admisión."""
    ADM = dict(ADMISION, paciente='DANNA SOFIA GOMEZ MELGAREJO', identificacion='TI 1122928591')
    DESC = ("Paciente no corresponde: documento 'DANNA SOFIA GOMEZ MELGAREJO', "
            "admisión 'DANNA SOFIA GOMEZ MELGAREJO'.")

    def _llamar(self, paciente_coincide, identificacion='1122928591'):
        import json
        from django.test import override_settings
        from gedocumental.services.ai_revision import _llamar_openai
        revision = {
            "nombre_documento": "DANNA SOFIA GOMEZ MELGAREJO", "identificacion_documento": identificacion,
            "paciente_coincide": paciente_coincide, "procedimientos_coinciden": "SI",
            "firma_paciente": "NO_APLICA", "tipo": "SOPORTES", "descripcion": self.DESC,
        }
        client = mock.MagicMock()
        client.chat.completions.create.return_value = mock.MagicMock(
            choices=[mock.MagicMock(message=mock.MagicMock(content=json.dumps(revision)))],
            usage=mock.MagicMock(prompt_tokens=1000, completion_tokens=100))
        with override_settings(OPENAI_API_KEY='x'), mock.patch('openai.OpenAI', return_value=client):
            return _llamar_openai('', ['img'], 'ORDEN', self.ADM)

    def test_mismo_paciente_no_es_hallazgo(self):
        for coincide in ('NO', 'SI'):
            self.assertEqual(self._llamar(coincide)['tipo'], 'OK', coincide)

    def test_cedula_del_responsable_no_es_otra_persona(self):
        r = self._llamar('NO', identificacion='40325527')
        self.assertNotIn('Paciente no corresponde', r['descripcion'])


class CantidadesIgualesTests(SimpleTestCase):
    """Revisión con la clave nueva: la IA lee 'CANTIDAD 2' pero deja el tipo AUTORIZACION."""
    ADM = dict(ADMISION, cups=['882317 - DOPPLER (cant. 2)'])

    def test_descripcion_dice_que_son_iguales(self):
        for desc in ("Cantidad autorizada en el documento es 2, mientras que la admisión es 2. Documento correcto.",
                     "Cantidad autorizada en el documento es 2, se esperaba 2 según admisión. Documento correcto.",
                     "Procedimiento no coincide: documento '891530 POTENCIALES EVOCADOS SOMATOSENSORIALES', "
                     "admisión '891530 POTENCIALES EVOCADOS SOMATOSENSORIALES'. Cantidad autorizada es 2, se esperaba 2."):
            self.assertEqual(evaluar_hallazgo('AUTORIZACION', desc, self.ADM)[0], DESCARTADO, desc)

    def test_distintas_o_con_otro_motivo_se_mantiene(self):
        for desc in ("Cantidad autorizada es 1, se esperaba 2 para el procedimiento 882317. Documento incorrecto.",
                     "Cantidad autorizada no coincide: documento 'CANTIDAD 2', admisión 'cantidad 2 y 1'.",
                     "Cantidad autorizada en el documento es 2, pero la admisión requiere 2. Procedimiento "
                     "autorizado no coincide con el de la admisión."):
            self.assertIsNone(evaluar_hallazgo('AUTORIZACION', desc, self.ADM), desc)
        # Zeus con otra cantidad: no se confía en la descripción
        desc = "Cantidad autorizada en el documento es 2, mientras que la admisión es 2."
        self.assertIsNone(evaluar_hallazgo('AUTORIZACION', desc, dict(ADMISION, cups=['882317 - DOPPLER (cant. 1)'])))

    def test_zeus_ya_tiene_la_cantidad_del_documento(self):
        # Caso 34348: autorización con 2 RX de rodilla; Zeus hoy factura 873420 con cantidad 2
        desc = ("Cantidad autorizada en el documento es 2, se esperaba 1 para el procedimiento 873420. "
                "Documento correcto en lo demás.")
        adm = dict(ADMISION, cups=['873420 - RADIOGRAFÍA DE RODILLA (AP- LATERAL) (cant. 2)'])
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', desc, adm)[0], CORREGIDO)
        adm2 = dict(ADMISION, cups=['873420 - RADIOGRAFÍA DE RODILLA (cant. 2)', '871121 - RX TÓRAX (cant. 1)'])
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', desc, adm2)[0], CORREGIDO)
        # Zeus sigue con 1: se mantiene
        self.assertIsNone(evaluar_hallazgo('AUTORIZACION', desc,
                                           dict(ADMISION, cups=['873420 - RADIOGRAFÍA DE RODILLA (cant. 1)'])))


class OrdenConMasCantidadTests(SimpleTestCase):
    def test_orden_que_pide_mas_sesiones(self):
        for desc in ("Procedimiento en documento: '992990 - INYECCIÓN O INFUSIÓN DE OTRA SUSTANCIA TERAPÉUTICA' "
                     "pero cantidad es 6, se esperaba 1.",
                     "Cantidad en documento (4) no coincide con la cantidad esperada en admisión (1) para el "
                     "procedimiento 992990."):
            self.assertEqual(evaluar_hallazgo('ORDEN', desc, ADMISION)[0], DESCARTADO, desc)

    def test_orden_con_menos_o_autorizacion_se_mantiene(self):
        self.assertIsNone(evaluar_hallazgo('ORDEN', "Cantidad en documento es 1, se esperaba 2.",
                                           dict(ADMISION, cups=['881302 - ECOGRAFÍA DE ABDOMEN TOTAL (cant. 2)'])))
        self.assertIsNone(evaluar_hallazgo('AUTORIZACION', "Cantidad autorizada es 6, se esperaba 1.", ADMISION))
        self.assertIsNone(evaluar_hallazgo('ORDEN', "Cantidad es 6, se esperaba 1; orden vencida.", ADMISION))


class CantidadAnotadaIATests(SimpleTestCase):
    """Revisión nueva de un escaneado como el 33486: la IA devuelve cantidad_anotada y el sistema compara con Zeus."""
    ADM = dict(ADMISION, cups=['873411 - RADIOGRAFÍA DE CADERA (cant. 2)', '873333 - RADIOGRAFÍA DE PIE (cant. 2)'])

    def _revisar(self, anotada, adm=None):
        import json
        from django.test import override_settings
        from gedocumental.services.ai_revision import _llamar_openai
        respuesta = {
            "paciente_documento": "JUAN ALEXIS BEJARANO PEÑA", "nombre_documento": "JUAN ALEXIS BEJARANO PEÑA",
            "identificacion_documento": "1121978991", "paciente_coincide": "SI",
            "procedimientos_documento": "873411; 873333", "procedimientos_coinciden": "SI",
            "firma_paciente": "NO_APLICA", "cantidad_anotada": anotada, "tipo": "AUTORIZACION",
            "descripcion": CantidadAnotadaTests.DESC,
        }
        client = mock.MagicMock()
        client.chat.completions.create.return_value.choices = [
            mock.MagicMock(message=mock.MagicMock(content=json.dumps(respuesta)))]
        with override_settings(OPENAI_API_KEY='x'), mock.patch('openai.OpenAI', return_value=client):
            return _llamar_openai('', ['img'], 'AUTORIZACION', adm or self.ADM)

    def test_anotacion_igual_a_zeus_queda_ok(self):
        self.assertEqual(self._revisar('2')['tipo'], 'OK')
        self.assertEqual(self._revisar('CANTIDAD 2')['tipo'], 'OK')

    def test_sin_anotacion_o_distinta_se_mantiene(self):
        self.assertEqual(self._revisar('')['tipo'], 'AUTORIZACION')
        self.assertEqual(self._revisar('3')['tipo'], 'AUTORIZACION')

    def test_zeus_con_cantidades_distintas_se_mantiene(self):
        adm = dict(ADMISION, cups=['873411 - RADIOGRAFÍA DE CADERA (cant. 2)', '873333 - RADIOGRAFÍA DE PIE (cant. 1)'])
        self.assertEqual(self._revisar('2', adm)['tipo'], 'AUTORIZACION')


class ContrasteTests(SimpleTestCase):
    """Caso 33457: orden de RM de abdomen y pelvis con contraste; los CUPS no dicen simple ni contrastado."""
    ADM = dict(ADMISION, cups=['883401 - RESONANCIA MAGNÉTICA DE ABDOMEN (cant. 1)',
                               '883440 - RESONANCIA MAGNÉTICA DE PELVIS (cant. 1)'])
    DESC = ("Documento correcto, pero la orden incluye contraste para la resonancia magnética de abdomen y "
            "pelvis, mientras que la admisión no especifica contraste.")

    def test_cups_que_no_distingue_contraste(self):
        self.assertEqual(evaluar_hallazgo('ORDEN', self.DESC, self.ADM)[0], DESCARTADO)
        self.assertEqual(evaluar_hallazgo('RESULTADO', self.DESC, self.ADM)[0], DESCARTADO)

    def test_redacciones_de_contraste_no_especificado(self):
        for fin in ("en la admisión no aparece contraste.", "la admisión no incluye el medio de contraste.",
                    "la admisión no menciona el uso de contraste."):
            desc = ("Documento correcto, pero la orden incluye contraste para la resonancia magnética de abdomen "
                    "y pelvis, mientras que " + fin)
            self.assertEqual(evaluar_hallazgo('ORDEN', desc, self.ADM)[0], DESCARTADO, desc)

    def test_cups_simple_y_orden_contrastada_se_mantiene(self):
        adm = dict(ADMISION, cups=['883101 - RESONANCIA MAGNÉTICA DE CEREBRO SIMPLE (cant. 1)'])
        desc = "La orden pide resonancia de cerebro con contraste y la admisión es simple."
        self.assertIsNone(evaluar_hallazgo('ORDEN', desc, adm))

    def test_contraste_con_otro_motivo_se_mantiene(self):
        for desc in ("La orden incluye contraste y no incluye la resonancia de pelvis.",
                     "Orden con contraste, vencida.",
                     "Incluye contraste; rodilla derecha y la admisión es izquierda."):
            self.assertIsNone(evaluar_hallazgo('ORDEN', desc, self.ADM), desc)
        self.assertIsNone(evaluar_hallazgo('COMPROBANTE', self.DESC, self.ADM))


class ProcedimientoAdicionalTests(SimpleTestCase):
    """Caso orden 759574: trae TAC de tórax (el de la admisión) y además un ecocardiograma."""
    ADM = dict(ADMISION, cups=['879301 - TOMOGRAFÍA COMPUTADA DE TÓRAX (cant. 1)'])
    DESC = ("Documento incorrecto: incluye ECOCARDIOGRAMA TRANSTORACICO, no esperado en admisión que solo "
            "tiene TOMOGRAFÍA COMPUTADA DE TÓRAX.")

    def test_orden_con_un_procedimiento_de_mas(self):
        self.assertEqual(evaluar_hallazgo('ORDEN', self.DESC, self.ADM)[0], DESCARTADO)
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', self.DESC, self.ADM)[0], DESCARTADO)

    def test_ia_lo_descarta_al_revisar(self):
        from gedocumental.services.ai_revision import _solo_procedimiento_adicional
        self.assertTrue(_solo_procedimiento_adicional(self.DESC))
        self.assertTrue(_solo_procedimiento_adicional("La orden incluye procedimientos adicionales a la admisión."))

    def test_examen_de_la_orden_que_no_se_tomo(self):
        # No es obligatorio tomar todos los exámenes de la orden: puede que solo se tome uno
        adm = dict(ADMISION, cups=['883401 - RESONANCIA MAGNÉTICA DE ABDOMEN (cant. 1)'])
        for desc in ("La orden incluye resonancia de abdomen y de pelvis; la de pelvis no aparece en la admisión.",
                     "La resonancia magnética de pelvis de la orden no está en la admisión.",
                     "Orden con RM de abdomen y RM de pelvis, esta última no fue facturada.",
                     "La orden incluye contraste y la RM de pelvis, que no se encuentra en la admisión."):
            self.assertEqual(evaluar_hallazgo('ORDEN', desc, adm)[0], DESCARTADO, desc)
        for desc in ("La resonancia de abdomen de la admisión no aparece en la orden.",
                     "La orden no incluye la resonancia de abdomen de la admisión; pide RM de tórax."):
            self.assertIsNone(evaluar_hallazgo('ORDEN', desc, adm), desc)

    def test_resultado_con_varios_estudios(self):
        # Un mismo informe trae RM de abdomen y RM de pelvis juntas
        adm = dict(ADMISION, cups=['883401 - RESONANCIA MAGNÉTICA DE ABDOMEN (cant. 1)'])
        for desc in (self.DESC, "El resultado incluye además resonancia de pelvis, no esperada en la admisión.",
                     "El informe trae RM de pelvis que no está en la admisión."):
            self.assertEqual(evaluar_hallazgo('RESULTADO', desc, adm)[0], DESCARTADO, desc)
        self.assertIsNone(evaluar_hallazgo('RESULTADO', "El resultado es de RM de tórax en lugar de abdomen.", adm))

    def test_abdomen_y_pelvis_van_juntos(self):
        from gedocumental.services.ai_revision import _mismo_estudio, _diferencia_clara
        rm = {'modalidad': 'RM', 'lateralidad': 'NO_ESPECIFICA', 'contraste': 'CON'}
        self.assertTrue(_mismo_estudio(dict(rm, region='PELVIS'), dict(rm, region='ABDOMEN')))
        self.assertFalse(_diferencia_clara(dict(rm, region='ABDOMEN'), dict(rm, region='PELVIS')))
        self.assertTrue(_diferencia_clara(dict(rm, region='TORAX'), dict(rm, region='ABDOMEN')))
        for adm_cups in ('883401 - RESONANCIA MAGNÉTICA DE ABDOMEN (cant. 1)',
                         '883440 - RESONANCIA MAGNÉTICA DE PELVIS (cant. 1)'):
            adm = dict(ADMISION, cups=[adm_cups])
            for desc in ("El resultado es de resonancia de abdomen y pelvis; la admisión es solo de abdomen.",
                         "La orden pide RM de pelvis, mientras que la admisión corresponde a RM de abdomen.",
                         "Resonancia magnética de abdomen y pelvis contrastada no coincide con el CUPS de la admisión."):
                self.assertEqual(evaluar_hallazgo('RESULTADO', desc, adm)[0], DESCARTADO, desc)
        adm = dict(ADMISION, cups=['883401 - RESONANCIA MAGNÉTICA DE ABDOMEN (cant. 1)'])
        for desc in ("La orden es de TAC de abdomen y pelvis; la admisión es resonancia de abdomen.",
                     "Orden de RM de abdomen y pelvis vencida.",
                     "El resultado es de RM de pelvis y tórax; la admisión es de abdomen."):
            self.assertIsNone(evaluar_hallazgo('ORDEN', desc, adm), desc)

    def test_faltante_se_mantiene(self):
        for desc in ("La orden no incluye TOMOGRAFÍA COMPUTADA DE TÓRAX; incluye ECOCARDIOGRAMA, no esperado.",
                     "Incluye ECOCARDIOGRAMA en lugar de TOMOGRAFÍA, no esperado en la admisión.",
                     "Incluye ECOCARDIOGRAMA, no esperado; orden vencida.",
                     "Incluye RX de rodilla derecha adicional, admisión izquierda."):
            self.assertIsNone(evaluar_hallazgo('ORDEN', desc, self.ADM), desc)


class DocumentoDeTercerosTests(SimpleTestCase):
    """Caso 33600: la orden del hospital dice 'MORENO RIDRIGUEZZ'; la autorización y Zeus, RODRIGUEZ."""
    ADM = dict(ADMISION, paciente='EITHAN MATTEO MORENO RODRIGUEZ', identificacion='RC 1234796155')
    NOMBRE = ("Nombre del usuario incorrecto: documento 'EITHAN MATTEO MORENO RIDRIGUEZ', "
              "admisión 'EITHAN MATTEO MORENO RODRIGUEZ'.")
    AUT_COMO_ZEUS = {'nombre': 'EITHAN MATTEO MORENO RODRIGUEZ', 'documento': '1234796155'}

    def test_orden_mal_escrita_y_autorizacion_como_zeus(self):
        r = evaluar_hallazgo('ORDEN', self.NOMBRE, self.ADM, autorizacion=self.AUT_COMO_ZEUS)
        self.assertEqual(r[0], DESCARTADO)
        self.assertIn('tercero', r[1])

    def test_autorizacion_con_otra_lectura_no_confirma_a_zeus(self):
        # La regla de terceros exige que la autorización coincida con Zeus (caso 33197); sin los demás
        # documentos no se decide
        aut = {'nombre': 'EITHAN MATTEO MORENO RODRIGEZ', 'documento': None}
        self.assertIsNone(evaluar_hallazgo('ORDEN', self.NOMBRE, self.ADM, autorizacion=aut))

    def test_autorizacion_igual_a_la_orden_el_error_es_de_zeus(self):
        # 32953: orden y autorización dicen BORRERO, Zeus BORERRO
        adm = dict(ADMISION, paciente='YERZON FERNEY BORERRO SANABRIA')
        desc = ("Nombre del usuario incorrecto: documento 'YERZON FERNEY BORRERO SANABRIA', "
                "admisión 'YERZON FERNEY BORERRO SANABRIA'.")
        aut = {'nombre': 'YERZON FERNEY BORRERO SANABRIA', 'documento': None}
        self.assertIsNone(evaluar_hallazgo('ORDEN', desc, adm, autorizacion=aut))

    def test_sin_autorizacion_revisada_no_decide(self):
        self.assertIsNone(evaluar_hallazgo('ORDEN', self.NOMBRE, self.ADM, autorizacion=None))

    def test_otra_persona_no_es_error_de_escritura(self):
        desc = "Nombre del usuario incorrecto: documento 'MAIRA JIMENEZ', admisión 'EITHAN MATTEO MORENO RODRIGUEZ'."
        self.assertIsNone(evaluar_hallazgo('ORDEN', desc, self.ADM, autorizacion=self.AUT_COMO_ZEUS))

    def test_la_autorizacion_misma_no_se_descarta_asi(self):
        self.assertIsNone(evaluar_hallazgo('AUTORIZACION', self.NOMBRE, self.ADM, autorizacion=self.AUT_COMO_ZEUS))

    def test_numero_mal_escrito_en_la_orden(self):
        # 33534 / 32870: un dígito cambiado o de menos; la autorización coincide con Zeus
        aut = {'nombre': ADMISION['paciente'], 'documento': '1121978991'}
        self.assertEqual(evaluar_hallazgo('ORDEN', DOC_MAL, ADMISION, autorizacion=aut)[0], DESCARTADO)
        desc = "Número de documento incorrecto: documento '4217991', admisión 'CC 40217991' (paciente X)."
        adm = dict(ADMISION, identificacion='CC 40217991')
        aut = {'nombre': ADMISION['paciente'], 'documento': '40217991'}
        self.assertEqual(evaluar_hallazgo('ORDEN', desc, adm, autorizacion=aut)[0], DESCARTADO)

    def test_numero_igual_en_orden_y_autorizacion_se_mantiene(self):
        aut = {'nombre': ADMISION['paciente'], 'documento': '1121979781'}
        self.assertIsNone(evaluar_hallazgo('ORDEN', DOC_MAL, ADMISION, autorizacion=aut))

    def test_numero_totalmente_distinto_se_mantiene(self):
        desc = "Número de documento incorrecto: documento '52000111', admisión 'RC 1121978991' (paciente X)."
        aut = {'nombre': ADMISION['paciente'], 'documento': '1121978991'}
        self.assertIsNone(evaluar_hallazgo('ORDEN', desc, ADMISION, autorizacion=aut))


class LecturaAutorizacionTests(SimpleTestCase):
    def _leer(self, revision):
        qs = mock.MagicMock()
        qs.filter.return_value.order_by.return_value.first.return_value = revision
        with mock.patch('gedocumental.models.RevisionIA.objects', qs):
            return lectura_autorizacion(33600, ADMISION)

    def test_sin_autorizacion(self):
        self.assertIsNone(self._leer(None))

    def test_autorizacion_ok_coincide_con_zeus(self):
        r = self._leer(SimpleNamespace(Resuelta=True, TipoHallazgo='OK', Descripcion='Documento correcto'))
        self.assertEqual(r, {'nombre': ADMISION['paciente'], 'documento': '1121978991'})

    def test_autorizacion_con_otro_motivo_coincide_con_zeus(self):
        r = self._leer(SimpleNamespace(Resuelta=False, TipoHallazgo='AUTORIZACION', Descripcion='Vencida'))
        self.assertEqual(r['documento'], '1121978991')

    def test_autorizacion_con_nombre_distinto(self):
        r = self._leer(SimpleNamespace(Resuelta=False, TipoHallazgo='INFORMACION', Descripcion=NOMBRE_MAL))
        self.assertEqual(r, {'nombre': 'JUAN ALEXIS BEJARAN PEÑA', 'documento': None})

    def test_autorizacion_con_numero_distinto(self):
        r = self._leer(SimpleNamespace(Resuelta=False, TipoHallazgo='INFORMACION', Descripcion=DOC_MAL))
        self.assertEqual(r['documento'], '1121979781')

    def test_autorizacion_de_otro_paciente(self):
        d = "Paciente no corresponde: documento 'MARIA LOPEZ', admisión 'X'."
        self.assertIsNone(self._leer(SimpleNamespace(Resuelta=False, TipoHallazgo='INFORMACION', Descripcion=d)))


class NombrePegadoYHistoriaClinicaTests(SimpleTestCase):
    """Caso 33587: la HC dice 'ALVAREZRODRIGUEZ PEDRO NEL'; Zeus 'PEDRO NEL ALVAREZ RODRIGUEZ'."""
    ADM = dict(ADMISION, paciente='PEDRO NEL ALVAREZ RODRIGUEZ', identificacion='CC 17317246')

    def test_nombre_pegado_es_el_mismo_paciente(self):
        from gedocumental.services.ai_revision import _mismo_paciente, _nombre_casi_igual, _tokens_nombre
        self.assertTrue(_mismo_paciente({'nombre_documento': 'ALVAREZRODRIGUEZ PEDRO NEL'}, self.ADM))
        self.assertTrue(_nombre_casi_igual(_tokens_nombre('ALVAREZRODRIGUEZ PEDRO NEL'),
                                           _tokens_nombre(self.ADM['paciente'])))
        self.assertTrue(_mismo_paciente({'nombre_documento': 'PEDRO NEL ALVAREZRODRIGUEZ'}, self.ADM))

    def test_pegado_con_otro_apellido_no_es_el_mismo(self):
        from gedocumental.services.ai_revision import _mismo_paciente
        self.assertFalse(_mismo_paciente({'nombre_documento': 'ALVAREZGOMEZ PEDRO NEL'}, self.ADM))

    def test_hallazgo_de_nombre_pegado_se_cierra(self):
        desc = ("Nombre del usuario incorrecto: documento 'ALVAREZRODRIGUEZ PEDRO NEL', "
                "admisión 'PEDRO NEL ALVAREZ RODRIGUEZ'.")
        self.assertEqual(evaluar_hallazgo('ORDEN', desc, self.ADM)[0], CORREGIDO)

    def test_historia_clinica_nombre_mal_escrito_no_es_hallazgo(self):
        desc = ("Nombre del usuario incorrecto: documento 'PEDRO NEL ALVARES RODRIGUEZ', "
                "admisión 'PEDRO NEL ALVAREZ RODRIGUEZ'.")
        for tipo in ('HCLINICA', 'HCNEURO', 'ADICIONALES'):
            self.assertEqual(evaluar_hallazgo(tipo, desc, self.ADM)[0], DESCARTADO)
        self.assertIsNone(evaluar_hallazgo('AUTORIZACION', desc, self.ADM))

    def test_historia_clinica_numero_mal_escrito_no_es_hallazgo(self):
        self.assertEqual(evaluar_hallazgo('HCLINICA', DOC_MAL, ADMISION)[0], DESCARTADO)

    def test_historia_clinica_de_otra_persona_sigue(self):
        desc = "Paciente no corresponde: documento 'MARIA LOPEZ GOMEZ', admisión 'PEDRO NEL ALVAREZ RODRIGUEZ'."
        self.assertIsNone(evaluar_hallazgo('HCLINICA', desc, self.ADM))


class LecturasDeOtrosDocumentosTests(SimpleTestCase):
    """Caso 33594: Zeus 21224247; la IA leyó 21242427 en la autorización y 21222447 en la orden."""
    ADM = dict(ADMISION, paciente='BETSY DEL CARMEN PADILLA DE OSPINA', identificacion='CC 21224247')
    AUT = ("Número de documento incorrecto: documento '21242427', admisión 'CC 21224247' "
           "(paciente BETSY DEL CARMEN PADILLA DE OSPINA).")
    ORDEN = ("Número de documento incorrecto: documento '21222447', admisión 'CC 21224247' "
             "(paciente BETSY DEL CARMEN PADILLA DE OSPINA).")

    def _otra(self, tipo, documento=None, nombre=None):
        return {'tipo': tipo, 'documento': documento, 'nombre': nombre}

    def test_cada_documento_dice_un_numero_distinto(self):
        otras = [self._otra('ORDEN', '21222447', self.ADM['paciente']),
                 self._otra('COMPROBANTE', '21224247', self.ADM['paciente'])]
        r = evaluar_hallazgo('AUTORIZACION', self.AUT, self.ADM, otras=otras)
        self.assertEqual(r[0], DESCARTADO)
        self.assertIn('Ningún otro documento', r[1])
        otras = [self._otra('AUTORIZACION', '21242427', self.ADM['paciente'])]
        self.assertEqual(evaluar_hallazgo('ORDEN', self.ORDEN, self.ADM, otras=otras)[0], DESCARTADO)

    def test_dos_documentos_con_el_mismo_numero_se_mantiene(self):
        otras = [self._otra('ORDEN', '21242427', self.ADM['paciente'])]
        self.assertIsNone(evaluar_hallazgo('AUTORIZACION', self.AUT, self.ADM, otras=otras))

    def test_sin_otros_documentos_no_decide(self):
        self.assertIsNone(evaluar_hallazgo('AUTORIZACION', self.AUT, self.ADM, otras=[]))
        self.assertIsNone(evaluar_hallazgo('AUTORIZACION', self.AUT, self.ADM,
                                           otras=[self._otra('ORDEN', None, self.ADM['paciente'])]))

    def test_numero_muy_distinto_se_mantiene(self):
        desc = "Número de documento incorrecto: documento '52000111', admisión 'CC 21224247' (paciente X)."
        otras = [self._otra('ORDEN', '21224247', self.ADM['paciente'])]
        self.assertIsNone(evaluar_hallazgo('AUTORIZACION', desc, self.ADM, otras=otras))

    def test_nombre_que_ningun_otro_documento_confirma(self):
        # 33600: autorización leída 'RODRIGEZ', orden 'RIDRIGUEZ', Zeus RODRIGUEZ
        adm = dict(ADMISION, paciente='EITHAN MATTEO MORENO RODRIGUEZ', identificacion='RC 1234796155')
        desc = ("Nombre del usuario incorrecto: documento 'EITHAN MATTEO MORENO RODRIGEZ', "
                "admisión 'EITHAN MATTEO MORENO RODRIGUEZ'.")
        otras = [self._otra('ORDEN', None, 'EITHAN MATTEO MORENO RIDRIGUEZ')]
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', desc, adm, otras=otras)[0], DESCARTADO)

    def test_nombre_que_otro_documento_confirma_el_error_es_de_zeus(self):
        # 32953: orden y autorización dicen BORRERO, Zeus BORERRO
        adm = dict(ADMISION, paciente='YERZON FERNEY BORERRO SANABRIA')
        desc = ("Nombre del usuario incorrecto: documento 'YERZON FERNEY BORRERO SANABRIA', "
                "admisión 'YERZON FERNEY BORERRO SANABRIA'.")
        otras = [self._otra('ORDEN', None, 'YERZON FERNEY BORRERO SANABRIA'),
                 self._otra('COMPROBANTE', '1121978991', 'YERZON FERNEY BORERRO SANABRIA')]
        self.assertIsNone(evaluar_hallazgo('AUTORIZACION', desc, adm, otras=otras))

    def test_una_palabra_distinta_y_otro_documento_confirma_zeus(self):
        # 33582: autorización leída 'SANTANA BEATRIZ'; orden y comprobante con SANTANA BENITO (Zeus)
        adm = dict(ADMISION, paciente='MARIA EDELMIRA SANTANA BENITO', identificacion='CC 21189487')
        desc = ("Nombre del usuario incorrecto: documento 'MARIA EDELMIRA SANTANA BEATRIZ', "
                "admisión 'MARIA EDELMIRA SANTANA BENITO'.")
        otras = [self._otra('ORDEN', '21189487', 'SANTANA BENITO MARIA EDELMIRA')]
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', desc, adm, otras=otras)[0], DESCARTADO)

    def test_una_palabra_distinta_cada_documento_distinto(self):
        adm = dict(ADMISION, paciente='MARIA EDELMIRA SANTANA BENITO', identificacion='CC 21189487')
        desc = ("Nombre del usuario incorrecto: documento 'MARIA EDELMIRA SANTANA BEATRIZ', "
                "admisión 'MARIA EDELMIRA SANTANA BENITO'.")
        # La orden escribe otra cosa: nadie respalda 'BEATRIZ', es error de escritura del documento
        otras = [self._otra('ORDEN', None, 'MARIA SANTANA LOPEZ')]
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', desc, adm, otras=otras)[0], DESCARTADO)
        otras = [self._otra('ORDEN', None, 'MARIA EDELMIRA SANTANA BEATRIZ')]   # la confirma
        self.assertIsNone(evaluar_hallazgo('AUTORIZACION', desc, adm, otras=otras))

    def test_varias_palabras_distintas_con_orden_igual_a_siesa(self):
        # La orden tiene el nombre de SIESA: el nombre mal escrito en la autorización no es hallazgo
        adm = dict(ADMISION, paciente='MARIA EDELMIRA SANTANA BENITO', identificacion='CC 21189487')
        desc = ("Nombre del usuario incorrecto: documento 'MARIA LUISA SANTANA BEATRIZ', "
                "admisión 'MARIA EDELMIRA SANTANA BENITO'.")
        otras = [self._otra('ORDEN', '21189487', adm['paciente'])]
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', desc, adm, otras=otras)[0], DESCARTADO)
        # Si ningún documento externo coincide con SIESA, sigue siendo hallazgo
        otras = [self._otra('ORDEN', '21189487', 'MARIA LUISA SANTANA BEATRIZ')]
        self.assertIsNone(evaluar_hallazgo('AUTORIZACION', desc, adm, otras=otras))

    def test_variante_parecida_en_varios_documentos_el_error_es_de_zeus(self):
        # 33197: Zeus ELVERT; orden y HC ELBERT; autorización leída ELBER
        adm = dict(ADMISION, paciente='ELVERT WALTER GOMEZ CARVAJAL')
        aut = ("Nombre del usuario incorrecto: documento 'ELBER WALTER GOMEZ CARVAJAL', "
               "admisión 'ELVERT WALTER GOMEZ CARVAJAL'.")
        otras = [self._otra('ORDEN', None, 'ELBERT WALTER GOMEZ CARVAJAL'),
                 self._otra('HCLINICA', None, 'ELBERT WALTER GOMEZ CARVAJAL'),
                 self._otra('COMPROBANTE', None, 'ELVERT WALTER GOMEZ CARVAJAL')]
        self.assertIsNone(evaluar_hallazgo('AUTORIZACION', aut, adm, otras=otras))
        orden = ("Nombre del usuario incorrecto: documento 'ELBERT WALTER GOMEZ CARVAJAL', "
                 "admisión 'ELVERT WALTER GOMEZ CARVAJAL'.")
        otras = [self._otra('AUTORIZACION', None, 'ELBER WALTER GOMEZ CARVAJAL'),
                 self._otra('HCLINICA', None, 'ELBERT WALTER GOMEZ CARVAJAL')]
        aut_lectura = {'nombre': 'ELBER WALTER GOMEZ CARVAJAL', 'documento': None}
        self.assertIsNone(evaluar_hallazgo('ORDEN', orden, adm, autorizacion=aut_lectura, otras=otras))

    def test_dos_variantes_distintas_en_un_solo_documento_cada_una(self):
        # 33600: autorización RODRIGEZ, orden RIDRIGUEZ (una sola variante parecida cada una) → error de lectura
        adm = dict(ADMISION, paciente='EITHAN MATTEO MORENO RODRIGUEZ')
        orden = ("Nombre del usuario incorrecto: documento 'EITHAN MATTEO MORENO RIDRIGUEZ', "
                 "admisión 'EITHAN MATTEO MORENO RODRIGUEZ'.")
        otras = [self._otra('AUTORIZACION', None, 'EITHAN MATTEO MORENO RODRIGEZ'),
                 self._otra('COMPROBANTE', None, 'EITHAN MATTEO MORENO RODRIGUEZ')]
        aut_lectura = {'nombre': 'EITHAN MATTEO MORENO RODRIGEZ', 'documento': None}
        self.assertEqual(evaluar_hallazgo('ORDEN', orden, adm, autorizacion=aut_lectura, otras=otras)[0],
                         DESCARTADO)

    def test_numero_parecido_en_varios_documentos_se_mantiene(self):
        otras = [self._otra('ORDEN', '21242428', self.ADM['paciente']),
                 self._otra('HCLINICA', '21242426', self.ADM['paciente'])]
        self.assertIsNone(evaluar_hallazgo('AUTORIZACION', self.AUT, self.ADM, otras=otras))

    def test_nombre_incompleto_en_el_documento_se_mantiene(self):
        # 33391: la orden solo dice 'MAIRA JIMENEZ'
        adm = dict(ADMISION, paciente='YOJEIDIS MARIA JIMENEZ MARTINEZ')
        desc = "Nombre del usuario incorrecto: documento 'MAIRA JIMENEZ', admisión 'YOJEIDIS MARIA JIMENEZ MARTINEZ'."
        otras = [self._otra('COMPROBANTE', None, adm['paciente'])]
        self.assertIsNone(evaluar_hallazgo('ORDEN', desc, adm, otras=otras))
        aut = {'nombre': adm['paciente'], 'documento': None}
        self.assertIsNone(evaluar_hallazgo('ORDEN', desc, adm, autorizacion=aut, otras=otras))

    def test_palabra_distinta_fuera_de_orden_se_mantiene(self):
        # 33081: 'GOMEZ HILDA MARIA' frente a Zeus 'HILDA GOMEZ BAREÑO'
        adm = dict(ADMISION, paciente='HILDA GOMEZ BAREÑO')
        desc = "Nombre del usuario incorrecto: documento 'GOMEZ HILDA MARIA', admisión 'HILDA GOMEZ BAREÑO'."
        otras = [self._otra('COMPROBANTE', None, adm['paciente'])]
        self.assertIsNone(evaluar_hallazgo('ORDEN', desc, adm, otras=otras))

    def test_zeus_con_el_nombre_incompleto_se_mantiene(self):
        # 33059: el documento trae un apellido que Zeus no tiene
        adm = dict(ADMISION, paciente='ANGEL MARIA AVILA')
        desc = "Nombre del usuario incorrecto: documento 'Ángel María Ávila Rivera', admisión 'ANGEL MARIA AVILA'."
        otras = [self._otra('COMPROBANTE', None, adm['paciente'])]
        self.assertIsNone(evaluar_hallazgo('ORDEN', desc, adm, otras=otras))

    def test_apellidos_primero_con_una_palabra_mal_leida(self):
        adm = dict(ADMISION, paciente='MARIA EDELMIRA SANTANA BENITO')
        desc = ("Nombre del usuario incorrecto: documento 'SANTANA BEATRIZ MARIA EDELMIRA', "
                "admisión 'MARIA EDELMIRA SANTANA BENITO'.")
        otras = [self._otra('ORDEN', None, adm['paciente'])]
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', desc, adm, otras=otras)[0], DESCARTADO)

    def test_lecturas_admision_ultima_revision_de_cada_archivo(self):
        revisiones = [
            SimpleNamespace(IdArchivo_id=1, TipoDocumento='AUTORIZACION', TipoHallazgo='INFORMACION',
                            Resuelta=False, Resolucion=None, NotaResolucion='', Descripcion=self.AUT),
            SimpleNamespace(IdArchivo_id=1, TipoDocumento='AUTORIZACION', TipoHallazgo='OK',
                            Resuelta=True, Resolucion='REEMPLAZADO', NotaResolucion='', Descripcion='Documento correcto'),
            SimpleNamespace(IdArchivo_id=2, TipoDocumento='COMPROBANTE', TipoHallazgo='OK',
                            Resuelta=True, Resolucion=None, NotaResolucion='', Descripcion='Documento correcto'),
            SimpleNamespace(IdArchivo_id=3, TipoDocumento='ORDEN', TipoHallazgo='INFORMACION', Resuelta=True,
                            Resolucion=DESCARTADO, NotaResolucion='El PDF tiene el número...', Descripcion=self.ORDEN),
        ]
        qs = mock.MagicMock()
        qs.filter.return_value.exclude.return_value.order_by.return_value = revisiones
        with mock.patch('gedocumental.models.RevisionIA.objects', qs):
            r = lecturas_admision(33594, self.ADM)
        self.assertEqual(r[1]['documento'], '21242427')      # la última revisión del archivo 1
        self.assertEqual(r[2]['documento'], '21224247')      # OK → Zeus
        self.assertEqual(r[3]['documento'], '21224247')      # la IA leyó mal (verificado en el PDF) → Zeus


class VariosMotivosTests(SimpleTestCase):
    def test_hallazgo_con_recaudo_no_se_cierra_por_una_parte(self):
        desc = DOC_MAL + " | No se recaudó; debía cobrarse cuota moderadora de $5.000"
        self.assertIsNone(evaluar_hallazgo('AUTORIZACION', desc, ADMISION, texto=TEXTO_ORDEN))

    def test_descripcion_vacia_u_otro_motivo(self):
        self.assertIsNone(evaluar_hallazgo('ORDEN', '', ADMISION, texto=TEXTO_ORDEN))
        self.assertIsNone(evaluar_hallazgo('AUTORIZACION', 'Autorización vencida', ADMISION, texto=TEXTO_ORDEN))
        self.assertFalse(necesita_pdf('AUTORIZACION', 'Autorización vencida'))


def _hallazgo(id_, descripcion, tipo='ORDEN', reverificado=None, archivo=1):
    return SimpleNamespace(IdRevision=id_, AdmisionId=33534, TipoDocumento=tipo, Descripcion=descripcion,
                           IdArchivo_id=archivo, FechaReverificacion=reverificado, Resuelta=False,
                           Resolucion=None, FechaResolucion=None, NotaResolucion='', save=mock.Mock())


class ReverificarHallazgosTests(SimpleTestCase):
    def _correr(self, hallazgos, texto=(TEXTO_ORDEN, True), datos=ADMISION, **kw):
        with mock.patch.object(reverificacion, '_pendientes', return_value=hallazgos), \
                mock.patch.object(reverificacion, 'obtener_contexto_admision', return_value=datos), \
                mock.patch.object(reverificacion, '_ruta', return_value='orden.pdf'),                 mock.patch.object(reverificacion, 'lectura_autorizacion', return_value=kw.pop('aut', None)), \
                mock.patch.object(reverificacion, 'lecturas_admision', return_value=kw.pop('lecturas', {})), \
                mock.patch.object(reverificacion, 'texto_pdf', return_value=texto) as leer:
            return reverificar_hallazgos_sin_ia(**kw), leer

    def test_descarta_y_guarda_la_nota(self):
        h = _hallazgo(1, DOC_MAL)
        r, _ = self._correr([h])
        self.assertEqual(r['resumen'][DESCARTADO], 1)
        self.assertTrue(h.Resuelta)
        self.assertEqual(h.Resolucion, DESCARTADO)
        self.assertIn('1121978991', h.NotaResolucion)
        self.assertIsNotNone(h.FechaReverificacion)
        campos = h.save.call_args.kwargs['update_fields']
        self.assertIn('NotaResolucion', campos)
        self.assertIn('FechaReverificacion', campos)

    def test_listar_no_guarda(self):
        h = _hallazgo(1, DOC_MAL)
        r, _ = self._correr([h], aplicar=False)
        self.assertEqual(r['resumen'][DESCARTADO], 1)
        self.assertFalse(h.Resuelta)
        h.save.assert_not_called()

    def test_corregido_en_zeus_no_abre_el_pdf(self):
        h = _hallazgo(1, DOC_MAL)
        r, leer = self._correr([h], datos=dict(ADMISION, identificacion='RC 1121979781'))
        self.assertEqual(r['resumen'][CORREGIDO], 1)
        leer.assert_not_called()

    def test_escaneado_queda_en_requieren_ia(self):
        h = _hallazgo(1, DOC_MAL)
        with mock.patch.object(reverificacion.os.path, 'exists', return_value=True):
            r, _ = self._correr([h], texto=('', False))
        self.assertEqual(r['resumen']['requieren_ia'], 1)
        self.assertEqual(r['resumen']['sin_archivo'], 0)
        self.assertFalse(h.Resuelta)
        self.assertEqual(h.save.call_args.kwargs['update_fields'], ['FechaReverificacion'])

    def test_archivo_no_encontrado(self):
        h = _hallazgo(1, DOC_MAL)
        r, _ = self._correr([h], texto=('', False))
        self.assertEqual(r['resumen']['sin_archivo'], 1)
        self.assertEqual(r['resumen']['requieren_ia'], 0)

    def test_pdf_con_paginas_escaneadas_no_descarta(self):
        h = _hallazgo(1, DOC_MAL)
        r, _ = self._correr([h], texto=(TEXTO_ORDEN, False))
        self.assertEqual(r['resumen'][DESCARTADO], 0)
        self.assertFalse(h.Resuelta)

    def test_no_relee_antes_de_24_horas(self):
        h = _hallazgo(1, DOC_MAL, reverificado=timezone.now() - timedelta(hours=2))
        r, leer = self._correr([h])
        leer.assert_not_called()
        self.assertFalse(h.Resuelta)
        r, leer = self._correr([h], forzar_pdf=True)
        leer.assert_called_once()
        self.assertTrue(h.Resuelta)

    def test_tope_de_tiempo(self):
        h = _hallazgo(1, DOC_MAL)
        r, leer = self._correr([h], max_segundos_pdf=0)
        leer.assert_not_called()
        self.assertFalse(h.Resuelta)

    def test_zeus_sin_respuesta_no_decide(self):
        h = _hallazgo(1, DOC_MAL)
        r, leer = self._correr([h], datos={'consecutivo': 33534})
        self.assertFalse(h.Resuelta)
        leer.assert_not_called()

    def test_orden_de_tercero_se_descarta_sin_abrir_el_pdf(self):
        h = _hallazgo(1, DOC_MAL)
        r, leer = self._correr([h], aut={'nombre': ADMISION['paciente'], 'documento': '1121978991'})
        self.assertEqual(r['resumen'][DESCARTADO], 1)
        self.assertIn('tercero', h.NotaResolucion)
        leer.assert_not_called()

    def test_lecturas_de_otros_archivos_sin_el_propio(self):
        h = _hallazgo(1, DOC_MAL)   # IdArchivo_id = 1
        lecturas = {1: {'tipo': 'ORDEN', 'documento': '1121979781', 'nombre': ''},
                    2: {'tipo': 'COMPROBANTE', 'documento': '1121978991', 'nombre': ''}}
        r, _ = self._correr([h], texto=('', False), lecturas=lecturas)
        self.assertEqual(r['resumen'][DESCARTADO], 1)
        self.assertIn('Ningún otro documento', h.NotaResolucion)

    def test_hallazgo_que_no_usa_pdf(self):
        h = _hallazgo(1, 'Autorización vencida', tipo='AUTORIZACION')
        r, leer = self._correr([h])
        leer.assert_not_called()
        h.save.assert_not_called()


class RecaudoTests(SimpleTestCase):
    def _zeus(self, **kw):
        base = {'empresa': 'EPS005', 'regimen': 1, 'contrato': 'SANITAS MRC', 'afiliado_zeus': 'BENEFICIARIO',
                'servicios': [('ECOGRAFIA', 100000.0)], 'valor_servicios': 100000.0, 'consultas': [False],
                'cuota_moderadora': 0.0, 'copago': 0.0}
        base.update(kw)
        return base

    def _verificar(self, doc, **zeus):
        with mock.patch.object(recaudo, 'datos_recaudo_zeus', return_value=self._zeus(**zeus)):
            return recaudo.verificar_recaudo(doc, 33419)

    def test_copago_correcto_beneficiario(self):
        self.assertIsNone(self._verificar({'requiere_pago': 'SI', 'categoria': 'B'}, copago=17300.0))

    def test_copago_cuando_era_cuota_moderadora(self):
        # Cotizante categoría A en un procedimiento: se cobra el valor de la categoría ($5.000) como copago;
        # cobrar el 11,5 % es un valor distinto
        r = self._verificar({'requiere_pago': 'SI', 'categoria': 'A'}, afiliado_zeus='COTIZANTE', copago=11500.0)
        self.assertEqual(r[0], 'RECAUDO')
        self.assertIn('Valor recaudado distinto', r[1])

    def test_procedimiento_sanitas_cotizante_es_copago(self):
        # Caso 35775: Sanitas contributivo, procedimiento, cotizante A; la autorización dice cuota moderadora
        # $5.000 y en SIESA se recaudó copago $5.000. La cuota moderadora es solo para consultas: está bien
        doc = {'requiere_pago': 'SI', 'categoria': 'A', 'tipo_cobro': 'CUOTA_MODERADORA', 'valor': '5000'}
        self.assertIsNone(self._verificar(doc, afiliado_zeus='COTIZANTE', copago=5000.0))
        self.assertIsNone(self._verificar({'requiere_pago': 'SI', 'categoria': 'A'},
                                          afiliado_zeus='COTIZANTE', copago=5000.0))
        # Una consulta de Sanitas cobrada como copago sigue siendo hallazgo de tipo
        r = self._verificar(doc, afiliado_zeus='COTIZANTE', copago=5000.0, consultas=[True])
        self.assertIn('Tipo de cobro incorrecto', r[1])

    def test_procedimiento_cobrado_como_copago_otra_eps(self):
        # Caso 34134 (Salud Total): la autorización dice cuota moderadora $5.000; en SIESA, copago $5.000
        otra = dict(empresa='EPS002', contrato='SALUD TOTAL CONTRIBUTIVO')
        doc = {'requiere_pago': 'SI', 'tipo_cobro': 'CUOTA_MODERADORA', 'valor': '5000'}
        self.assertIsNone(self._verificar(doc, copago=5000.0, **otra))
        self.assertIn('Valor recaudado distinto', self._verificar(doc, copago=20000.0, **otra)[1])
        # En una consulta va cuota moderadora: el copago sí es tipo incorrecto
        r = self._verificar(doc, copago=5000.0, consultas=[True], **otra)
        self.assertIn('Tipo de cobro incorrecto', r[1])

    def test_sanitas_no_valida_tipo_de_afiliado(self):
        # Casos 34368 (MRC) y 34398 (Evento): en Sanitas el tipo de afiliado no se ve en el pantallazo;
        # vale el de Zeus
        doc = {'requiere_pago': 'SI', 'categoria': 'B', 'tipo_afiliado': 'COTIZANTE'}
        self.assertIsNone(self._verificar(doc, copago=17300.0))
        self.assertIsNone(self._verificar(doc, copago=17300.0, contrato='SANITAS EVENTO CONTRIBUTIVO'))
        # Por ahora tampoco en otras EPS; con VALIDAR_TIPO_AFILIADO vuelve a marcarse fuera de Sanitas
        doc = {'requiere_pago': 'NO', 'tipo_afiliado': 'COTIZANTE'}
        otra = dict(empresa='EPS002', contrato='SALUD TOTAL CONTRIBUTIVO')
        self.assertIsNone(self._verificar(doc, **otra))
        with mock.patch.object(recaudo, 'VALIDAR_TIPO_AFILIADO', True):
            self.assertIn('Tipo de afiliado en Zeus beneficiario', self._verificar(doc, **otra)[1])

    def test_no_se_recaudo(self):
        r = self._verificar({'requiere_pago': 'SI', 'categoria': 'A'}, consultas=[True])
        self.assertIn('No se recaudó', r[1])

    def test_exoneracion(self):
        r = self._verificar({'exoneracion': 'SI'}, cuota_moderadora=5000.0)
        self.assertIn('exoneración', r[1])
        self.assertIsNone(self._verificar({'exoneracion': 'SI'}))

    def test_documento_sin_datos_de_pago(self):
        self.assertIsNone(self._verificar({'requiere_pago': 'NO_INDICA'}))

    def test_valores_en_texto(self):
        self.assertEqual(recaudo._numero('20.100'), 20100)
        self.assertEqual(recaudo._numero('$ 5,000'), 5000)
        self.assertEqual(recaudo._numero('11.5'), 11.5)
        self.assertIsNone(recaudo._numero('NO_INDICA'))
        self.assertEqual(recaudo._numero('0.115'), 0.115)

    def test_porcentaje_copago_con_valor_pesos_cero(self):
        # "Recaudo de pago: 0.115 | Porcentaje Copago: 11.5 | Valor pesos pago compartido: 0.0"
        for doc in ({'requiere_pago': 'NO', 'tipo_cobro': 'COPAGO', 'valor': '0.0', 'porcentaje': '11.5'},
                    {'requiere_pago': 'NO_INDICA', 'valor': '0.0', 'porcentaje': '11.5'},
                    {'requiere_pago': 'SI', 'tipo_cobro': 'COPAGO', 'valor': '0.115', 'porcentaje': ''},
                    {'requiere_pago': 'SI', 'tipo_cobro': 'COPAGO', 'porcentaje': '0.115'}):
            self.assertIsNone(self._verificar(doc, copago=11500.0), doc)
        r = self._verificar({'requiere_pago': 'NO', 'valor': '0.0', 'porcentaje': '11.5'})
        self.assertIn('No se recaudó', r[1])

    def test_salud_total_valor_cero_porcentaje_100(self):
        # Caso 34300: "Tipo Recaudo: Copago | Valor: 0 | Porcentaje: 100% | Valor Maximo: 0.0000"
        otra = dict(empresa='EPS002', contrato='SALUD TOTAL CONTRIBUTIVO', valor_servicios=756800.0)
        for doc in ({'requiere_pago': 'SI', 'tipo_cobro': 'COPAGO', 'valor': '0', 'porcentaje': '100'},
                    {'requiere_pago': 'NO', 'tipo_cobro': 'COPAGO', 'valor': '0', 'porcentaje': '100%'}):
            self.assertIsNone(self._verificar(doc, **otra), doc)
        r = self._verificar({'requiere_pago': 'NO', 'valor': '0', 'porcentaje': '100'}, copago=5000.0, **otra)
        self.assertIn('no requiere pago', r[1])

    def test_no_requiere_pago_con_cobro(self):
        r = self._verificar({'requiere_pago': 'NO', 'valor': '0', 'porcentaje': '0'}, copago=15400.0)
        self.assertIn('no requiere pago', r[1])

    def test_copago_sanitas_leido_como_sin_pago(self):
        # Caso 34617: "Copago", "Recaudo de pago: 0.173" partido por el salto de página; la IA leyó valor 0
        doc = {'requiere_pago': 'NO', 'tipo_cobro': 'COPAGO', 'valor': '0.0', 'porcentaje': '0.0'}
        rm = dict(servicios=[('RESONANCIA MAGNETICA DE CEREBRO', 472919.0)], valor_servicios=472919.0)
        self.assertIsNone(self._verificar(doc, copago=81815.0, **rm))
        # Lo cobrado no es un porcentaje de Sanitas: sí es hallazgo
        self.assertIn('no requiere pago', self._verificar(doc, copago=50000.0, **rm)[1])

    def test_cuota_moderadora_con_recaudo_y_porcentaje_cero(self):
        # Caso 34409 (Sanitas): "Cuota Moderadora", "Recaudo de pago: 0", "Porcentaje Copago: 0.0"
        doc = {'requiere_pago': 'SI', 'tipo_cobro': 'CUOTA_MODERADORA', 'valor': '0', 'porcentaje': '0.0',
               'categoria': 'A'}
        self.assertIsNone(self._verificar(doc, consultas=[True]))
        self.assertIn('no requiere pago', self._verificar(doc, consultas=[True], cuota_moderadora=5000.0)[1])


class DatosOrdenPacienteTests(SimpleTestCase):
    """Médico, IPS y fecha de ordenamiento y nivel educativo (variables dinámicas de la admisión en Zeus)."""

    def _faltantes(self, **cambios):
        from datetime import datetime
        from gedocumental.services.validacion_paciente import _evaluar
        fila = dict(tipo_id='TI', num_id='1122928591', nombre='DANNA', apellido='GOMEZ',
                    fecha_naci=datetime(2010, 6, 23), sexo='F', identidad=2, orientacion=None, etnia=7,
                    comunidad='', discapacidad=8, cod_dep='50', cod_muni='001', pais_res='170', nacionalidad=170,
                    direccion='CALLE 6 # 26-30', telefono='3102677920', celular='',
                    zona='01', zona_rips='U', id_cargo='9998', cargo='',
                    fecha_ing=datetime(2026, 10, 21), medico='DRA KARINA GONZALES', ips='SANITAS DE LA 36',
                    fecha_orden='2026/10/21', nivel='3', contrato=15, tiene_autorizacion=1)
        fila.update(cambios)
        return {f['campo']: f['motivo'] for f in _evaluar(tuple(fila.values()), {15, 4})['faltantes']}

    def test_completo(self):
        self.assertEqual(self._faltantes(), {})

    def test_campos_vacios(self):
        f = self._faltantes(medico='', ips=None, fecha_orden='', nivel=None)
        self.assertEqual(set(f), {'Médico que ordena', 'IPS que ordena', 'Fecha de ordenamiento', 'Nivel educativo'})

    def test_fecha_de_ordenamiento(self):
        self.assertEqual(self._faltantes(fecha_orden='2026/09/22'), {})
        self.assertIn('Posterior al ingreso', self._faltantes(fecha_orden='2029/09/11')['Fecha de ordenamiento'])
        self.assertEqual(self._faltantes(fecha_orden='22/09')['Fecha de ordenamiento'], 'Fecha no válida')

    def test_numero_de_autorizacion(self):
        from datetime import datetime
        hoy = dict(fecha_ing=datetime.now(), fecha_orden=f"{datetime.now():%Y/%m/%d}")
        # Contrato que lo exige (Capital Salud = 15) sin número: hallazgo
        self.assertEqual(list(self._faltantes(tiene_autorizacion=0, **hoy)),
                         ['No cuenta con número de autorización en SIESA'])
        # Contrato que no lo lleva (FOMAG = 21, particular...): no se marca
        self.assertEqual(self._faltantes(tiene_autorizacion=0, contrato=21, **hoy), {})
        # Admisión antigua (radicación de documentos meses después): no se marca
        from datetime import datetime
        self.assertEqual(self._faltantes(tiene_autorizacion=0, fecha_ing=datetime(2026, 1, 10),
                                         fecha_orden='2026/01/10'), {})

    def test_ocupacion_solo_en_texto(self):
        # Caso 35306: la pantalla de citas guardó la ocupación en 'cargo' y dejó id_cargo vacío
        self.assertEqual(self._faltantes(id_cargo='', cargo='Jubilado, desempleado, ama de casa, estudiante, de'), {})
        self.assertIn('Ocupación', self._faltantes(id_cargo='', cargo=''))
        self.assertIn('Ocupación', self._faltantes(id_cargo='', cargo='En los casos en que no se tiene esta información'))

    def test_zona_territorial_y_ocupacion(self):
        self.assertEqual(self._faltantes(zona=None, zona_rips='R'), {})
        f = self._faltantes(zona='', zona_rips=None, id_cargo='9999')
        self.assertEqual(set(f), {'Zona territorial', 'Ocupación'})
        self.assertIn('Ocupación', self._faltantes(id_cargo=None))


class AlertasTelegramTests(SimpleTestCase):
    def setUp(self):
        from gedocumental.services import alertas
        self.alertas = alertas
        self.registro = os.path.join(tempfile.mkdtemp(), 'alertas.json')
        p1 = mock.patch.object(alertas, 'REGISTRO_ENVIOS', self.registro)
        p2 = mock.patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 't', 'TELEGRAM_CHAT_ID': '1, 2'})
        p3 = mock.patch('requests.post', return_value=mock.MagicMock(ok=True))
        p1.start(), p2.start()
        self.post = p3.start()
        self.addCleanup(mock.patch.stopall)

    def test_una_alerta_cada_seis_horas(self):
        self.assertTrue(self.alertas.alertar('x', 'IA apagada'))
        self.assertEqual(self.post.call_count, 2)   # un mensaje por chat
        self.assertFalse(self.alertas.alertar('x', 'IA apagada'))
        self.assertTrue(self.alertas.alertar('y', 'otra'))

    def test_clasifica_errores_de_openai(self):
        class AuthenticationError(Exception):
            pass
        class RateLimitError(Exception):
            pass
        self.alertas.alertar_error_openai(AuthenticationError("Error code: 401 - invalid_api_key"))
        self.alertas.alertar_error_openai(RateLimitError("Error code: 429 - {'code': 'insufficient_quota'}"))
        self.alertas.alertar_error_openai(RateLimitError("Error code: 429 - rate limit reached"))  # pasajero
        textos = [c.kwargs['json']['text'] for c in self.post.call_args_list]
        self.assertEqual(len(textos), 4)
        self.assertIn('inválida', textos[0])
        self.assertIn('saldo', textos[2])

    def test_ia_apagada_avisa(self):
        from django.test import override_settings
        from gedocumental.services.ai_revision import _ia_disponible
        with override_settings(REVISAR_CON_IA=False):
            self.assertFalse(_ia_disponible())
        self.assertIn('APAGADA', self.post.call_args.kwargs['json']['text'])


class ParticularSinValidacionTests(SimpleTestCase):
    """Caso 34600: particular con orden manuscrita; sus documentos no se validan."""
    ADM = dict(ADMISION, paciente='HECTURIEL RODRIGUEZ GONZALES', contrato='PARTICULAR TARIFA PLENA')

    def test_no_llama_a_la_ia(self):
        from gedocumental.services import ai_revision
        archivo = SimpleNamespace(Tipo='ORDEN', IdArchivo=1, Admision_id=34600)
        with mock.patch.object(ai_revision, '_registrar_revision') as registrar, \
                mock.patch.object(ai_revision, 'analizar_documento') as analizar:
            r = ai_revision.revisar_archivo(archivo, self.ADM)
        analizar.assert_not_called()
        registrar.assert_called_once()
        self.assertEqual(r['tipo'], 'OK')

    def test_hallazgos_abiertos_se_descartan(self):
        desc = "Paciente no corresponde: documento 'HECTURIEL BALDERRAMA', admisión 'HECTURIEL RODRIGUEZ GONZALES'."
        self.assertEqual(evaluar_hallazgo('ORDEN', desc, self.ADM)[0], DESCARTADO)
        self.assertIsNone(evaluar_hallazgo('ORDEN', desc, dict(self.ADM, contrato='SANITAS EVENTO CONTRIBUTIVO')))


class RecaudoDescripcionVigenteTests(SimpleTestCase):
    """Caso 34368: el hallazgo de tipo de afiliado se actualiza al motivo de recaudo que sigue vigente."""

    def _correr(self, descripcion, tipo, nuevo):
        from gedocumental.services import ai_revision
        h = SimpleNamespace(IdRevision=1, AdmisionId=34368, TipoHallazgo=tipo, Descripcion=descripcion,
                            DatosRecaudo='{"porcentaje": "11.5"}', save=mock.MagicMock())
        qs = mock.MagicMock()
        qs.exclude.return_value = [h]
        with mock.patch('gedocumental.models.RevisionIA.objects.filter', return_value=qs), \
                mock.patch.object(recaudo, 'verificar_recaudo', return_value=nuevo):
            ai_revision.reverificar_recaudo_con_zeus()
        return h

    def test_texto_viejo_se_reemplaza(self):
        nuevo = ('RECAUDO', 'Valor recaudado distinto: copago $122.600; debía ser copago de $142.581')
        h = self._correr('Tipo de afiliado en Zeus beneficiario, la autorización dice cotizante', 'INFORMACION', nuevo)
        self.assertEqual((h.TipoHallazgo, h.Descripcion), nuevo)
        # Con otro motivo de la IA, solo se cambia la parte del recaudo
        h = self._correr('Autorización vencida | Tipo de afiliado en Zeus beneficiario', 'AUTORIZACION', nuevo)
        self.assertEqual((h.TipoHallazgo, h.Descripcion), ('AUTORIZACION', 'Autorización vencida | ' + nuevo[1]))


class SinDiferenciasTests(SimpleTestCase):
    """Caso 35625 / 35237: la IA marca hallazgo comparando datos iguales."""

    def test_comparaciones_iguales(self):
        from gedocumental.services.ai_revision import _sin_diferencias
        for d in ("Cantidad autorizada incorrecta: documento 'CANTIDAD 2', admisión 'CANTIDAD 2'; procedimientos no "
                  "coinciden: documento '930860, 891509', admisión '930860, 891509'.",
                  "Paciente coincide: documento 'ROMERO_MORENO,GLADYS_', admisión 'GLADYS ROMERO MORENO'.",
                  "Paciente coincide: documento 'ROMERO_MORENO,GLADYS_', admisión 'GLADYS ROMERO MORENO'. "
                  "Documento correcto."):
            self.assertTrue(_sin_diferencias(d), d)
            self.assertEqual(evaluar_hallazgo('AUTORIZACION', d, ADMISION)[0], DESCARTADO)

    def test_diferencia_real_u_otro_motivo(self):
        from gedocumental.services.ai_revision import _sin_diferencias
        for d in ("Cantidad autorizada incorrecta: documento 'CANTIDAD 8', admisión 'CANTIDAD 1'.",
                  "Procedimiento no coincide: documento '883101', admisión '883210'.",
                  "Paciente coincide: documento 'GLADYS ROMERO', admisión 'GLADYS ROMERO'. Autorización vencida.",
                  "Autorización vencida."):
            self.assertFalse(_sin_diferencias(d), d)


class OrdenManuscritaTests(SimpleTestCase):
    """Caso 19671: orden escrita a mano; la IA leyó 'MARIO CAICEDO' por 'Antonio Caicedo'."""
    ADM = dict(ADMISION, paciente='FERNANDO ANTONIO CAICEDO FLOREZ', identificacion='CC 79262907')

    def _llamar(self, manuscrito, tipo='ORDEN'):
        import json
        from django.test import override_settings
        from gedocumental.services.ai_revision import _llamar_openai
        revision = {"nombre_documento": "MARIO CAICEDO", "identificacion_documento": "",
                    "paciente_coincide": "NO", "procedimientos_coinciden": "SI", "firma_paciente": "NO_APLICA",
                    "manuscrito": manuscrito, "tipo": "INFORMACION",
                    "descripcion": "Paciente no corresponde: documento 'MARIO CAICEDO', admisión 'FERNANDO ANTONIO CAICEDO FLOREZ'."}
        client = mock.MagicMock()
        client.chat.completions.create.return_value = mock.MagicMock(
            choices=[mock.MagicMock(message=mock.MagicMock(content=json.dumps(revision)))],
            usage=mock.MagicMock(prompt_tokens=1000, completion_tokens=50))
        with override_settings(OPENAI_API_KEY='x'), mock.patch('openai.OpenAI', return_value=client):
            return _llamar_openai('', ['img'], tipo, self.ADM)

    def test_orden_manuscrita_no_tiene_hallazgos(self):
        r = self._llamar('SI')
        self.assertEqual(r['tipo'], 'OK')
        self.assertTrue(r['ajuste_manuscrita'])

    def test_impresa_u_otro_documento_se_valida(self):
        self.assertEqual(self._llamar('NO')['tipo'], 'INFORMACION')
        self.assertEqual(self._llamar('SI', tipo='AUTORIZACION')['tipo'], 'INFORMACION')
