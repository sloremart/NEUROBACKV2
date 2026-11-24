"""
Tests sin base de datos (SimpleTestCase): reglas de reverificación de hallazgos sin IA y del recaudo.
  python manage.py test gedocumental
"""

import os
import tempfile
from datetime import datetime, timedelta
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


class OrdenLlenadaAManoTests(SimpleTestCase):
    """Caso 36508: fórmula Colsanitas impresa llenada a mano; 'TI 1029993091' leído '702996901'."""
    ADM = dict(ADMISION, paciente='DANNA ISABELLA LINARES REINA', identificacion='TI 1029993091')

    def _llamar(self, segunda):
        import json
        from django.test import override_settings
        from gedocumental.services.ai_revision import _llamar_openai
        revision = {"nombre_documento": "LINARES REINA DANNA ISABELLA", "identificacion_documento": "702996901",
                    "paciente_coincide": "NO", "procedimientos_coinciden": "SI", "firma_paciente": "NO_APLICA",
                    "tipo": "INFORMACION", "manuscrito": "NO",
                    "descripcion": "Número de documento incorrecto: documento '702996901'."}

        def respuesta(contenido):
            return mock.MagicMock(choices=[mock.MagicMock(message=mock.MagicMock(content=json.dumps(contenido)))],
                                  usage=mock.MagicMock(prompt_tokens=100, completion_tokens=10))
        client = mock.MagicMock()
        client.chat.completions.create.side_effect = [respuesta(revision), respuesta(segunda)]
        with override_settings(OPENAI_API_KEY='x'), mock.patch('openai.OpenAI', return_value=client):
            return _llamar_openai('', ['img'], 'ORDEN', self.ADM)

    def test_llenada_a_mano_pasa(self):
        r = self._llamar({"manuscrito": "SI"})
        self.assertEqual(r['tipo'], 'OK')

    def test_impresa_sigue_con_hallazgo(self):
        self.assertNotEqual(self._llamar({"manuscrito": "NO"})['tipo'], 'OK')


class RecienNacidoTests(SimpleTestCase):
    """Caso 36536: orden 'VANDERWERFF GUTIERREZ HIJA DE MARILYN STEPHANY' (CN); SIESA 'SARA LOPEZ VANDERWERFF'."""
    ADM = dict(ADMISION, paciente='SARA LOPEZ VANDERWERFF', identificacion='RC 1253259727', cups=[])

    def test_no_es_hallazgo(self):
        from gedocumental.services.ai_revision import _clasificar_paciente, _es_recien_nacido
        for nombre in ('Hija de Marilyn', 'VANDERWERFF GUTIERREZ HIJA DE MARILYN STEPHANY', 'RN DE ANA PEREZ',
                       'RECIEN NACIDO PEREZ', 'HIJO DEL SR GOMEZ'):
            self.assertTrue(_es_recien_nacido(nombre), nombre)
            self.assertEqual(_clasificar_paciente({'nombre_documento': nombre,
                                                   'identificacion_documento': '26101010349079'}, '', self.ADM),
                             'misma_persona', nombre)
        for nombre in ('MARIA HIJAR PEREZ', 'ARNOLDO ROJAS', 'RENATA NUNEZ'):
            self.assertFalse(_es_recien_nacido(nombre), nombre)

    def test_hallazgo_existente_se_cierra(self):
        desc = "Paciente no corresponde: documento 'Hija de Marilyn', admisión 'SARA LOPEZ VANDERWERFF'."
        self.assertEqual(evaluar_hallazgo('ORDEN', desc, self.ADM)[0], DESCARTADO)


class NombreCortadoPorLaEpsTests(SimpleTestCase):
    """Caso 36811: Capital Salud imprime 'YEIDY ANDREA RIVERA CASTI' (cortado); SIESA 'YEIDY ANDREA RIVEROS CASTILLO'."""
    ADM = {'paciente': 'YEIDY ANDREA RIVEROS CASTILLO', 'identificacion': 'CC 1120964198'}

    def c(self, nombre, ident=''):
        from gedocumental.services.ai_revision import _clasificar_paciente
        return _clasificar_paciente({'nombre_documento': nombre, 'identificacion_documento': ident}, '', self.ADM)

    def test_nombre_cortado_no_es_hallazgo(self):
        self.assertEqual(self.c('YEIDY ANDREA RIVERA CASTI'), 'misma_persona')
        self.assertEqual(self.c('YEIDY ANDREA RIVERA CASTI-TR'), 'misma_persona')
        self.assertEqual(self.c('YEIDY ANDREA RIVEROS CASTI'), 'misma_persona')
        self.assertEqual(self.c('YEIDY ANDREA RIVERA CASTI', '1120964198'), 'misma_persona')
        # Sin corte, la diferencia de letras sigue siendo "nombre mal escrito"
        self.assertEqual(self.c('YEIDY ANDREA RIVERA CASTILLO'), 'nombre_mal_escrito')

    def test_beneficiario_con_edad_y_documento_de_la_mama(self):
        # Caso 36270: 'BENEF. ANA LUCIA PEREZ ROJA-HI EDAD 01 MES', RC 1000000001 y CC 1000000002 (cotizante)
        from gedocumental.services.ai_revision import _clasificar_paciente
        adm = {'paciente': 'ANA LUCIA PEREZ ROJAS', 'identificacion': 'RC 1000000001'}
        for nombre in ('ANA LUCIA PEREZ ROJA-HI EDAD 01 MES', 'BENEF. ANA LUCIA PEREZ ROJA-HI'):
            for ident in ('', '1000000002', '1000000001'):
                self.assertEqual(_clasificar_paciente({'nombre_documento': nombre, 'identificacion_documento': ident},
                                                      '', adm), 'misma_persona', (nombre, ident))
        desc = ("Paciente no corresponde: documento 'ANA LUCIA PEREZ ROJA-HI EDAD 01 MES', admisión 'ANA LUCIA PEREZ ROJAS'.")
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', desc, dict(ADMISION, cups=[], **adm))[0], DESCARTADO)

    def test_otra_persona_sigue(self):
        self.assertEqual(self.c('MARIA ANDREA RIVERA CASTILLO'), 'otra_persona')
        self.assertEqual(self.c('YEIDY RIVERA'), 'otra_persona')


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
        # + 1 consulta corta: ¿la orden está llenada a mano? (caso 36508)
        self.assertEqual((llamadas, r['llamadas'], r['tokens']), (3, 2, [75000, 900]))


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
        self.assertEqual((r['tipo'], llamadas), ('SOPORTES', 3))   # + ¿manuscrita?

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


class AutorizacionDeMasConOrdenTests(SimpleTestCase):
    """Caso 36734: autorización 883101 x2; orden y admisión x1 → la admisión está bien."""
    ADM = dict(ADMISION, paciente='DEMECIO HERNANDEZ COCA', identificacion='CC 86044398',
               cups=['883101 - RESONANCIA MAGNETICA DE CEREBRO (cant. 1)'], cantidades_orden='883101:1')
    DESC = "Cantidad autorizada en el documento es 2, se esperaba 1 para el procedimiento 883101."

    def test_orden_igual_a_admision_se_descarta(self):
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', self.DESC, self.ADM)[0], DESCARTADO)
        desc = "Cantidad autorizada incorrecta: documento '2', admisión '1'."
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', desc, self.ADM)[0], DESCARTADO)

    def test_con_afirmaciones_de_la_ia(self):
        # Caso 35704: la IA confirma nombre y procedimiento antes del motivo de cantidad
        desc = ("Nombre del usuario correcto; procedimiento autorizado '883101 - RESONANCIA T-CONTRASTE MAGNETICA "
                "DE CEREBRO' coincide con la admisión. Cantidad autorizada es 2, se esperaba 1.")
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', desc, self.ADM)[0], DESCARTADO)
        self.assertIsNone(evaluar_hallazgo('AUTORIZACION', desc.replace('correcto', 'incorrecto'), self.ADM))

    def test_autorizacion_de_mas_ya_no_es_hallazgo(self):
        # Regla 2026-11-19 (caso 35852): facturar menos de lo autorizado no es hallazgo de la AUTORIZACION,
        # aunque la orden pida más (eso lo revisa la ORDEN)
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', self.DESC,
                                          dict(self.ADM, cantidades_orden='883101:2'))[0], DESCARTADO)

    def test_caso_35852_emg_autorizada_8_facturada_4(self):
        from gedocumental.services.ai_revision import _autorizacion_de_mas, _cantidades_distintas_zeus
        adm = dict(ADMISION, cups=[
            '930860-4 - ELECTROMIOGRAFÍA EN CADA EXTREMIDAD (cant. 4)',
            '891509-16 - NEUROCONDUCCIÓN (CADA NERVIO) 16 NERVIOS (cant. 10)',
            '891509 - NEUROCONDUCCIÓN (CADA NERVIO) (cant. 6)'])
        desc = "Cantidad autorizada en el documento es 8, se esperaba 4 para el procedimiento 930860."
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', desc, adm)[0], DESCARTADO)
        self.assertTrue(_autorizacion_de_mas(desc, adm, '930860:8; 891509:6; 891509:10'))
        # Facturar más de lo autorizado sí es hallazgo
        self.assertFalse(_autorizacion_de_mas(desc, adm, '930860:2; 891509:16'))
        self.assertFalse(_autorizacion_de_mas(
            "Cantidad autorizada en el documento es 2, se esperaba 4 para el procedimiento 930860.", adm))
        self.assertEqual(_cantidades_distintas_zeus('930860:8; 891509:6; 891509:10', adm), [])

    def test_sin_cantidades_de_la_orden_tambien_se_descarta(self):
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', self.DESC, dict(self.ADM, cantidades_orden=''))[0],
                         DESCARTADO)

    def test_autorizacion_con_menos_sigue(self):
        adm = dict(self.ADM, cups=['883101 - RESONANCIA MAGNETICA DE CEREBRO (cant. 2)'], cantidades_orden='883101:2')
        desc = "Cantidad autorizada en el documento es 1, se esperaba 2 para el procedimiento 883101."
        self.assertIsNone(evaluar_hallazgo('AUTORIZACION', desc, adm))

    def test_post_proceso_ia(self):
        from gedocumental.services.ai_revision import _autorizacion_de_mas_con_orden
        self.assertTrue(_autorizacion_de_mas_con_orden(self.DESC, self.ADM, '883101:2'))
        self.assertFalse(_autorizacion_de_mas_con_orden(self.DESC, self.ADM, '883101:1'))


def _curva(x0, top, ancho, alto, pts=12):
    return {'x0': x0, 'x1': x0 + ancho, 'top': top, 'bottom': top + alto, 'pts': [(0, 0)] * pts}


class FirmaVectorialTests(SimpleTestCase):
    """Comprobante firmado en tablet e 'impreso' a PDF: todo son vectores, sin texto ni imágenes (caso 33421)."""

    _curva = staticmethod(_curva)

    def _detectar(self, curvas):
        from gedocumental.services.ai_revision import _detectar_firma_pdf
        pagina = SimpleNamespace(width=595, height=842, images=[], chars=[], curves=curvas,
                                 extract_words=lambda: [])
        pdf = mock.MagicMock()
        pdf.__enter__.return_value = SimpleNamespace(pages=[pagina])
        with mock.patch('pdfplumber.open', return_value=pdf):
            return _detectar_firma_pdf('/tmp/33421C.pdf')

    # Letras vectorizadas: muchas curvas pequeñas por toda la página
    LETRAS = [_curva(40 + (i % 60) * 8, 60 + (i // 60) * 14, 7, 9) for i in range(1700)]

    def test_firma_en_trazos_vectoriales(self):
        firma = [self._curva(80, 640, 120, 45, pts=60), self._curva(150, 650, 40, 30, pts=20)]
        self.assertTrue(self._detectar(self.LETRAS + firma))

    def test_sin_firma_solo_letras(self):
        self.assertIsNone(self._detectar(self.LETRAS))

    def test_trazo_alto_en_el_encabezado_no_es_firma(self):
        self.assertIsNone(self._detectar(self.LETRAS + [self._curva(40, 30, 80, 40, pts=40)]))

    def test_firma_al_inicio_de_la_segunda_pagina(self):
        """Casos 32828 y 35203: la firma pasa a la 2.ª hoja y queda arriba."""
        from gedocumental.services.ai_revision import _detectar_firma_pdf
        hoja = lambda curvas: SimpleNamespace(width=595, height=842, images=[], chars=[], curves=curvas,
                                              extract_words=lambda: [])
        pdf = mock.MagicMock()
        pdf.__enter__.return_value = SimpleNamespace(pages=[
            hoja(self.LETRAS), hoja([self._curva(60, 40, 200, 60, pts=80)])])
        with mock.patch('pdfplumber.open', return_value=pdf):
            self.assertTrue(_detectar_firma_pdf('/tmp/35203C.pdf'))

    def test_firma_en_segunda_pagina_con_rotulo_en_texto(self):
        from gedocumental.services.ai_revision import _detectar_firma_pdf
        rotulo = [{'text': 'Firma', 'top': 140, 'bottom': 150, 'x0': 60, 'x1': 90}]
        pagina2 = SimpleNamespace(width=595, height=842, images=[], chars=[{}] * 40,
                                  curves=[self._curva(60, 40, 200, 60, pts=80)], extract_words=lambda: rotulo)
        hoja1 = SimpleNamespace(width=595, height=842, images=[], chars=[], curves=self.LETRAS,
                                extract_words=lambda: [])
        pdf = mock.MagicMock()
        pdf.__enter__.return_value = SimpleNamespace(pages=[hoja1, pagina2])
        with mock.patch('pdfplumber.open', return_value=pdf):
            self.assertTrue(_detectar_firma_pdf('/tmp/32828C.pdf'))

    def test_resultado_con_lado_se_relee(self):
        self.assertTrue(necesita_pdf('RESULTADO', ResultadoMiembroInferiorTests.DESC))


class AplicacionMedicamentoTests(SimpleTestCase):
    """Caso 33971: FOMAG autoriza 992990 x2 (Alirocumab) y la admisión factura 1 aplicación."""

    ADM = dict(ADMISION, cups=['992990 - INYECCIÓN O INFUSIÓN DE OTRA SUSTANCIA TERAPÉUTICA O PROFILÁCTICA (cant. 1)'])
    DESC = "Cantidad autorizada en el documento es 2, se esperaba 1 para el procedimiento 992990."

    def test_autorizacion_con_mas_aplicaciones_no_es_hallazgo(self):
        decision = evaluar_hallazgo('AUTORIZACION', self.DESC, self.ADM)
        self.assertEqual(decision[0], 'DESCARTADO')

    def test_autorizacion_con_menos_si_es_hallazgo(self):
        adm = dict(ADMISION, cups=['992990 - INYECCIÓN O INFUSIÓN DE OTRA SUSTANCIA TERAPÉUTICA (cant. 3)'])
        desc = "Cantidad autorizada en el documento es 2, se esperaba 3 para el procedimiento 992990."
        self.assertIsNone(evaluar_hallazgo('AUTORIZACION', desc, adm))

    def test_otro_procedimiento_no_aplica(self):
        from gedocumental.services.ai_revision import _autorizacion_aplicacion_de_mas
        adm = dict(ADMISION, cups=['883101 - RESONANCIA MAGNÉTICA DE CEREBRO (cant. 1)'])
        desc = "Cantidad autorizada en el documento es 2, se esperaba 1 para el procedimiento 883101."
        self.assertFalse(_autorizacion_aplicacion_de_mas(desc, adm))


class DocumentosRequeridosTests(SimpleTestCase):
    """Documentos exigidos por contrato (pestaña Documentos faltantes, bandeja CM, Gestión de facturación)."""

    def f(self, contrato, consulta, tipos, primera_vez=False):
        from gedocumental.services.documentos_requeridos import faltantes
        return faltantes(contrato, consulta, tipos, primera_vez)

    def test_eps_exige_los_tres(self):
        for contrato in ('CAPITAL SALUD SUBSIDIADO', 'SANITAS EVENTO CONTRIBUTIVO', 'FOMAG', 'SALUD TOTAL CONTRIBUTIVO',
                         'EJERCITO', 'COMPENSAR', 'MULTISALUD IPS', 'IPS ONCOLIFE', 'POLICIA ESTUDIOS DEL SUEÑO'):
            self.assertEqual(self.f(contrato, False, set()), ['ORDEN', 'AUTORIZACION', 'COMPROBANTE'], contrato)

    def test_admision_solo_de_sedacion_no_exige_documentos(self):
        # Caso 36991: 998702 (soporte de sedación) se factura aparte de la resonancia; no se pide nada
        from gedocumental.services.documentos_requeridos import faltantes
        self.assertEqual(faltantes('SANITAS EVENTO CONTRIBUTIVO', False, set(), sedacion=True), [])
        self.assertEqual(faltantes('SANITAS EVENTO CONTRIBUTIVO', False, set()), ['ORDEN', 'AUTORIZACION', 'COMPROBANTE'])

    def test_admision_de_anestesiologia_no_se_valida(self):
        # Caso 15798: consulta de anestesiología con la orden de la resonancia → no es hallazgo
        from gedocumental.services.ai_revision import _es_anestesia
        from gedocumental.services.reverificacion import DESCARTADO, evaluar_hallazgo
        adm = {'cups': ['890226 - CONSULTA DE PRIMERA VEZ POR ESPECIALISTA EN ANESTESIOLOGIA (cant. 1)'],
               'contrato': 'FOMAG', 'paciente': 'ANA PEREZ ROJAS', 'identificacion': '1000000001'}
        self.assertTrue(_es_anestesia(adm))
        self.assertTrue(_es_anestesia(dict(adm, cups=['998702 - SOPORTE DE SEDACION', '998701 - SOPORTE ANESTESICO'])))
        self.assertFalse(_es_anestesia(dict(adm, cups=['883108 - RESONANCIA MAGNETICA DE PARES CRANEANOS',
                                                       '998702 - SOPORTE DE SEDACION'])))
        self.assertFalse(_es_anestesia(dict(adm, cups=[])))
        d = ("Procedimiento no coincide: la orden no trae el CUPS de la admisión. Admisión: 890226 (CONSULTA DE "
             "PRIMERA VEZ POR ESPECIALISTA EN ANESTESIOLOGIA). La orden trae: 883108 (RESONANCIA MAGNETICA DE "
             "PARES CRANEANOS).")
        r = evaluar_hallazgo('ORDEN', d, adm)
        self.assertEqual(r[0], DESCARTADO)

    def test_sedacion_cuenta_como_con_autorizacion(self):
        from gedocumental.services.validacion_paciente import CAMPOS_SQL, SQL_SOLO_SEDACION
        self.assertIn("'998702'", SQL_SOLO_SEDACION)
        self.assertIn(SQL_SOLO_SEDACION, CAMPOS_SQL)

    def test_bolivar_poliza_sin_autorizacion(self):
        self.assertEqual(self.f('BOLIVAR POLIZA', False, set()), ['ORDEN', 'COMPROBANTE'])
        self.assertEqual(self.f('BOLIVAR POLIZA', False, {'ORDEN', 'COMPROBANTE'}), [])
        self.assertEqual(self.f('BOLIVAR SOAT', False, set()), ['ORDEN', 'AUTORIZACION', 'COMPROBANTE'])
        # Colmédica Línea Zafiro y Colsanitas Plan Integral: sin autorización (regla 2026-11-19)
        self.assertEqual(self.f('COLMEDICA LINEA ZAFIRO', False, set()), ['ORDEN', 'COMPROBANTE'])
        self.assertEqual(self.f('AXA COLPATRIA SEGUROS S.A.', False, set()), ['ORDEN', 'COMPROBANTE'])
        self.assertEqual(self.f('COLMEDICA LINEA RUBI', False, set()), ['ORDEN', 'AUTORIZACION', 'COMPROBANTE'])
        self.assertEqual(self.f('COLSANITAS PLAN INTEGRAL', True, set()), ['ORDEN', 'COMPROBANTE'])
        # Plan Integral: consulta de primera vez sin orden; control con orden
        self.assertEqual(self.f('COLSANITAS PLAN INTEGRAL', True, set(), True), ['COMPROBANTE'])
        self.assertEqual(self.f('COLSANITAS PLAN MODULAR', True, set(), True), ['ORDEN', 'AUTORIZACION', 'COMPROBANTE'])
        self.assertEqual(self.f('COLSANITAS PLAN INTEGRAL', False, set()), ['COMPROBANTE'])
        self.assertEqual(self.f('COLSANITAS PLAN MODULAR', False, set()), ['AUTORIZACION', 'COMPROBANTE'])

    def test_solimed_solo_comprobante(self):
        self.assertEqual(self.f('IPS SOLIMED JD SAS', False, set()), ['COMPROBANTE'])
        self.assertEqual(self.f('IPS SOLIMED JD SAS', True, {'COMPROBANTE'}), [])

    def test_colsanitas_procedimiento_sin_orden(self):
        self.assertEqual(self.f('COLSANITAS PLAN INTEGRAL', False, {'AUTORIZACION', 'COMPROBANTE'}), [])

    def test_colsanitas_consulta_exige_orden(self):
        self.assertEqual(self.f('COLSANITAS PLAN MODULAR', True, {'AUTORIZACION', 'COMPROBANTE'}), ['ORDEN'])

    def test_ips_consultorio_orden_o_autorizacion(self):
        c = 'IPS CONSULTORIO MEDICO SALUD OCUPACIONAL S.A.S'
        self.assertEqual(self.f(c, False, {'AUTORIZACION', 'COMPROBANTE'}), [])
        self.assertEqual(self.f(c, False, {'ORDEN', 'COMPROBANTE'}), [])
        self.assertEqual(self.f(c, False, {'COMPROBANTE'}), ['ORDEN o AUTORIZACION'])
        equipo = 'EQUIPO INTERDISCIPLINARIO PARA EL MEJORAMIENTO DE LA CALIDAD'
        self.assertEqual(self.f(equipo, False, {'ORDEN', 'COMPROBANTE'}), [])
        self.assertEqual(self.f(equipo, True, {'AUTORIZACION', 'COMPROBANTE'}), [])

    def test_mrc_y_particular(self):
        self.assertEqual(self.f('SANITAS MRC SUBSIDIADO', False, {'ORDEN', 'COMPROBANTE'}), [])
        self.assertEqual(self.f('PARTICULAR TARIFA PLENA', False, set()), [])


class ResultadoMiembroInferiorTests(SimpleTestCase):
    """Caso 35203: CUPS 883522 'RM de articulaciones de miembro inferior' y el informe es de cadera derecha."""

    ADM = dict(ADMISION, cups=['883522 - RESONANCIA MAGNÉTICA DE ARTICULACIONES DE MIEMBRO INFERIOR '
                               '(ESPECÍFICO) (cant. 1)'])
    DESC = ("El documento reporta RESONANCIA DE CADERA DERECHA CONTRASTADA, mientras que la admisión es "
            "RESONANCIA MAGNÉTICA DE ARTICULACIONES DE MIEMBRO INFERIOR.")

    def test_cadera_es_miembro_inferior(self):
        texto = "RESULTADO DE IMAGENES DIAGNOSTICAS LECTURA RESONANCIA DE CADERA DERECHA CONTRASTADA " * 3
        decision = evaluar_hallazgo('RESULTADO', self.DESC, self.ADM, texto=texto)
        self.assertEqual(decision[0], 'DESCARTADO')

    def test_hombro_no_es_miembro_inferior(self):
        texto = "RESULTADO DE IMAGENES DIAGNOSTICAS LECTURA RESONANCIA DE HOMBRO DERECHO " * 3
        desc = self.DESC.replace('CADERA DERECHA CONTRASTADA', 'HOMBRO DERECHO')
        self.assertIsNone(evaluar_hallazgo('RESULTADO', desc, self.ADM, texto=texto))

    def test_lado_del_cups_debe_coincidir(self):
        adm = dict(ADMISION, cups=['883522 - RESONANCIA DE MIEMBRO INFERIOR IZQUIERDO (cant. 1)'])
        texto = "RESULTADO DE IMAGENES DIAGNOSTICAS LECTURA RESONANCIA DE RODILLA DERECHA " * 3
        self.assertIsNone(evaluar_hallazgo('RESULTADO', self.DESC, adm, texto=texto))


class CantidadCeroLeidaTests(SimpleTestCase):
    """Caso 35942: Sanitas, CUPS 879420 cantidad 1 (columna corrida); la IA leyó 0."""
    ADM = dict(ADMISION, paciente='OLGA LILIANA TOLE MONTES', identificacion='CC 40428075',
               cups=['879420 - TOMOGRAFIA COMPUTADA DE ABDOMEN Y PELVIS (cant. 1)'])

    def test_cantidad_cero_se_descarta(self):
        for desc in ("La autorización no cubre el procedimiento de la admisión, ya que la cantidad autorizada es 0 "
                     "para el CUPS 879420, mientras que se esperaba 1.",
                     "Cantidad autorizada incorrecta: documento '0', admisión '1'."):
            self.assertEqual(evaluar_hallazgo('AUTORIZACION', desc, self.ADM)[0], DESCARTADO, desc)

    def test_otras_cantidades_y_motivos_siguen(self):
        from gedocumental.services.ai_revision import _cantidad_cero_leida
        self.assertFalse(_cantidad_cero_leida("Cantidad autorizada incorrecta: documento '10', admisión '1'."))
        self.assertFalse(_cantidad_cero_leida("Cantidad autorizada es 2, se esperaba 1."))
        self.assertFalse(_cantidad_cero_leida("Autorización vencida; cantidad autorizada es 0."))
        self.assertFalse(_cantidad_cero_leida("La autorización no cubre el CUPS 879420 (documento trae 879410)."))


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

    ADM_REAL = dict(ADMISION, paciente='LEIDY CAROLINA LOPEZ COCUY', identificacion='CC 40305485', cups=[
        '883440 - RESONANCIA MAGNÉTICA DE PELVIS (cant. 1)', 'None - VENOCAT IV NO. 20 ROSADO (cant. 1)',
        'None - CLARISCAN 0.5MMOL (ACIDO GADOTERICO) (cant. 1)',
        'None - JERINGA INYECTOR DE CONTRASTE SINO MEDICAL -RESONADOR (cant. 1)'])

    def test_contrastada_sin_observacion_ya_no_es_hallazgo(self):
        # Regla 2026-11-19: mismo CUPS simple/contrastado, la admisión puede ir sin observación
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', self.DESC, self.ADM_REAL)[0], DESCARTADO)
        d = ("Contraste: la orden indica contrastada ('CONTRASTADA') y la admisión en SIESA no tiene la "
             "observación de contrastada (el CUPS es el mismo para simple y contrastado).")
        self.assertEqual(evaluar_hallazgo('ORDEN', d, self.ADM_REAL)[0], DESCARTADO)

    def test_admision_con_observacion_contrastada_se_descarta(self):
        adm = dict(self.ADM_REAL, observacion='RM PELVIS CONTRASTADA')
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', self.DESC, adm)[0], DESCARTADO)

    def test_ia_ok_pero_documento_contrastado_sin_observacion(self):
        from gedocumental.services.ai_revision import _falta_observacion_contraste
        r = {'contraste_documento': 'SS RNM DE PELVIS CONTRASTADA'}
        self.assertTrue(_falta_observacion_contraste('AUTORIZACION', r, '', self.ADM_REAL))
        self.assertFalse(_falta_observacion_contraste('AUTORIZACION', r, '',
                                                      dict(self.ADM_REAL, observacion='contrastada')))
        self.assertFalse(_falta_observacion_contraste('AUTORIZACION', {'contraste_documento': 'sin contraste'},
                                                      '', self.ADM_REAL))
        self.assertFalse(_falta_observacion_contraste('AUTORIZACION', {'contraste_documento': ''}, '',
                                                      self.ADM_REAL))
        self.assertFalse(_falta_observacion_contraste('RESULTADO', r, '', self.ADM_REAL))

    def test_nombre_incorrecto_sigue_siendo_otro_motivo(self):
        from gedocumental.services.ai_revision import _solo_motivo_contraste
        adm = {'cups': ['883440 - RESONANCIA MAGNETICA DE PELVIS (cant. 1)']}
        self.assertFalse(_solo_motivo_contraste("Nombre del usuario incorrecto. Contraste distinto.", adm))

    def test_documento_sin_contraste_se_descarta(self):
        # Solo difiere el contraste pero el documento no lo pide: el CUPS no distingue, no es error
        desc = "Nombre del usuario correcto. Contraste en documento es SIN y en admisión no especifica."
        adm = dict(ADMISION, cups=['883440 - RESONANCIA MAGNETICA DE PELVIS (cant. 1)'])
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', desc, adm)[0], DESCARTADO)


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

    def test_firma_de_un_familiar_y_apellido_de_mas(self):
        # Caso 35533: la IA tomó la firma 'LEIDY CATALINA VILLAMIL CARDONA'; la relectura trae el impreso con
        # CARDONA, que SIESA no tiene → nombre incompleto en SIESA, no otra persona
        adm = dict(self.ADM, paciente='NICOL XIMENA VILLAMIL', identificacion='TI 1122520760')
        import json
        from django.test import override_settings
        from gedocumental.services.ai_revision import _llamar_openai
        revision = {"nombre_documento": "LEIDY CATALINA VILLAMIL CARDONA", "identificacion_documento": "1121853266",
                    "paciente_coincide": "NO", "procedimientos_coinciden": "SI", "firma_paciente": "SI",
                    "tipo": "INFORMACION", "descripcion": "Paciente no corresponde."}
        def respuesta(contenido):
            return mock.MagicMock(choices=[mock.MagicMock(message=mock.MagicMock(content=json.dumps(contenido)))],
                                  usage=mock.MagicMock(prompt_tokens=100, completion_tokens=10))
        client = mock.MagicMock()
        client.chat.completions.create.side_effect = [respuesta(revision),
                                                      respuesta({"nombre": "NICOL XIMENA VILLAMIL CARDONA"})]
        with override_settings(OPENAI_API_KEY='x'), mock.patch('openai.OpenAI', return_value=client):
            r = _llamar_openai('', ['img'], 'AUTORIZACION', adm)
        self.assertEqual(r['paciente_coincide'], 'NOMBRE_MAL_ESCRITO')
        self.assertIn("NICOL XIMENA VILLAMIL CARDONA", r['descripcion'])
        self.assertTrue(r['descripcion'].startswith('Nombre del usuario incorrecto'))

    def test_firma_de_un_familiar_con_documento_impreso(self):
        # Caso 35533 (autorización): la IA y la relectura toman la firma 'Leidy Cardona'; el TI de la admisión
        # está impreso en el documento → es la paciente
        adm = dict(self.ADM, paciente='NICOL XIMENA VILLAMIL', identificacion='TI 1122520760')
        import json
        from django.test import override_settings
        from gedocumental.services.ai_revision import _llamar_openai
        revision = {"nombre_documento": "Leidy Cardona", "identificacion_documento": "1121853266",
                    "paciente_coincide": "NO", "procedimientos_coinciden": "SI", "firma_paciente": "SI",
                    "tipo": "INFORMACION", "descripcion": "Paciente no corresponde."}
        def respuesta(contenido):
            return mock.MagicMock(choices=[mock.MagicMock(message=mock.MagicMock(content=json.dumps(contenido)))],
                                  usage=mock.MagicMock(prompt_tokens=100, completion_tokens=10))
        client = mock.MagicMock()
        # 1.ª relectura (página completa): otra vez la firma; 2.ª (parte de arriba): el TI impreso
        client.chat.completions.create.side_effect = [
            respuesta(revision),
            respuesta({"nombre": "Leidy Cardona", "identificaciones": ["1121853266"]}),
            respuesta({"nombre": "NICOL XIMENA VILLAMIL CARDONA", "identificaciones": ["1,122,520,760"]})]
        import base64, io
        from PIL import Image
        buffer = io.BytesIO()
        Image.new('RGB', (100, 140), 'white').save(buffer, format='PNG')
        pagina = base64.b64encode(buffer.getvalue()).decode()
        with override_settings(OPENAI_API_KEY='x'), mock.patch('openai.OpenAI', return_value=client):
            r = _llamar_openai('', [pagina], 'AUTORIZACION', adm)
        self.assertEqual(client.chat.completions.create.call_count, 3)
        recorte = client.chat.completions.create.call_args.kwargs['messages'][1]['content'][1]['image_url']['url']
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(recorte.split(',')[1]))).size, (100, 77))
        # Nombre impreso con CARDONA que SIESA no tiene: es la paciente, con el nombre incompleto en SIESA
        self.assertEqual(r['paciente_coincide'], 'NOMBRE_MAL_ESCRITO')
        self.assertTrue(r['descripcion'].startswith('Nombre del usuario incorrecto'))

    def test_documento_impreso_aunque_el_nombre_siga_mal(self):
        adm = dict(self.ADM, paciente='NICOL XIMENA VILLAMIL', identificacion='TI 1122520760')
        import json
        from django.test import override_settings
        from gedocumental.services.ai_revision import _llamar_openai
        revision = {"nombre_documento": "Leidy Cardona", "identificacion_documento": "1121853266",
                    "paciente_coincide": "NO", "procedimientos_coinciden": "SI", "firma_paciente": "SI",
                    "tipo": "INFORMACION", "descripcion": "Paciente no corresponde."}
        def respuesta(contenido):
            return mock.MagicMock(choices=[mock.MagicMock(message=mock.MagicMock(content=json.dumps(contenido)))],
                                  usage=mock.MagicMock(prompt_tokens=100, completion_tokens=10))
        client = mock.MagicMock()
        client.chat.completions.create.side_effect = [
            respuesta(revision),
            respuesta({"nombre": "Leidy Cardona", "identificaciones": ["1,122,520,760", "1121853266"]})]
        with override_settings(OPENAI_API_KEY='x'), mock.patch('openai.OpenAI', return_value=client):
            r = _llamar_openai('', ['img'], 'AUTORIZACION', adm)
        self.assertEqual(r['tipo'], 'OK')
        self.assertEqual(r['paciente_coincide'], 'SI')

    def test_relectura_con_otra_persona_sigue(self):
        r = self._llamar({"nombre": "CAMILO HERNANDEZ RUBIO"})
        self.assertTrue(r['descripcion'].startswith('Paciente no corresponde: documento'))


class OrdenLargaTranscripcionTests(SimpleTestCase):
    """Caso 36691: orden con resonancia, consulta 890374 y laboratorios; la admisión solo tiene la consulta."""

    def test_transcripcion_cortada_igual_encuentra_el_cups(self):
        import json
        from django.test import override_settings
        from gedocumental.services.ai_revision import _llamar_openai
        adm = dict(ADMISION, paciente='HERALDO RODRIGUEZ HUGO', identificacion='CC 19240528',
                   cups=['890374 - CONSULTA DE CONTROL O DE SEGUIMIENTO POR ESPECIALISTA EN NEUROLOGIA (cant. 1)'])
        revision = {"nombre_documento": "HERALDO RODRIGUEZ HUGO", "identificacion_documento": "19240528",
                    "paciente_coincide": "SI", "procedimientos_coinciden": "NO", "firma_paciente": "NO_APLICA",
                    "tipo": "SOPORTES", "procedimientos_documento": "",
                    "descripcion": ("Procedimientos no coinciden: documento incluye resonancia y otros procedimientos, "
                                    "admisión solo incluye consulta de neurología."),
                    "estudio_documento": {"modalidad": "RM", "region": "CEREBRO"},
                    "estudio_admision": {"modalidad": "CONSULTA", "especialidad": "NEUROLOGIA"}}
        def respuesta(contenido):
            return mock.MagicMock(choices=[mock.MagicMock(message=mock.MagicMock(content=contenido))],
                                  usage=mock.MagicMock(prompt_tokens=100, completion_tokens=10))
        client = mock.MagicMock()
        client.chat.completions.create.side_effect = [
            respuesta(json.dumps(revision)),
            # JSON cortado por el tope de tokens
            respuesta('{"procedimientos": "883101 RESONANCIA MAGNETICA DE CEREBRO; 890374 CONSULTA DE CONTROL; '
                      '944301 TERAPIA DE REHABILITACION; 902210 HEMOGRAMA IV [HEMOGLOBINA, HEMA')]
        with override_settings(OPENAI_API_KEY='x'), mock.patch('openai.OpenAI', return_value=client):
            r = _llamar_openai('', ['img'], 'ORDEN', adm)
        self.assertEqual(r['tipo'], 'OK')
        self.assertTrue(r.get('ajuste_transcripcion'))

    def test_cups_distinto_mensaje_claro(self):
        # La admisión real era 890274 (primera vez) y la orden 890374 (control): el mensaje lo dice con códigos
        import json
        from django.test import override_settings
        from gedocumental.services.ai_revision import _llamar_openai
        adm = dict(ADMISION, paciente='HERALDO RODRIGUEZ HUGO', identificacion='CC 19240528',
                   cups=['890274 - CONSULTA DE PRIMERA VEZ POR ESPECIALISTA EN NEUROLOGIA (cant. 1)'])
        revision = {"nombre_documento": "HERALDO RODRIGUEZ HUGO", "identificacion_documento": "19240528",
                    "paciente_coincide": "SI", "procedimientos_coinciden": "NO", "firma_paciente": "NO_APLICA",
                    "tipo": "SOPORTES", "procedimientos_documento": "",
                    "descripcion": ("Procedimientos no coinciden: documento incluye resonancia magnética y otros, "
                                    "admisión solo incluye consulta de neurología."),
                    "estudio_documento": {"modalidad": "RM", "region": "CEREBRO"},
                    "estudio_admision": {"modalidad": "CONSULTA", "especialidad": "NEUROLOGIA"}}
        def respuesta(contenido):
            return mock.MagicMock(choices=[mock.MagicMock(message=mock.MagicMock(content=contenido))],
                                  usage=mock.MagicMock(prompt_tokens=100, completion_tokens=10))
        client = mock.MagicMock()
        client.chat.completions.create.side_effect = [
            respuesta(json.dumps(revision)),
            respuesta(json.dumps({"procedimientos": "883101-RESONANCIA NUCLEAR MAGNETICA DE CEREBRO (#1) / SIMPLE; "
                                  "890374-CONSULTA DE CONTROL O DE SEGUIMIENTO POR ESPECIALISTA EN NEUROLOGIA (#1); "
                                  "903700-VITAMINA D (#1)"}))]
        with override_settings(OPENAI_API_KEY='x'), mock.patch('openai.OpenAI', return_value=client):
            r = _llamar_openai('', ['img'], 'ORDEN', adm)
        self.assertEqual(r['tipo'], 'SOPORTES')
        self.assertIn('Admisión: 890274 (CONSULTA DE PRIMERA VEZ', r['descripcion'])
        self.assertIn('La orden trae: 890374 (CONSULTA DE CONTROL', r['descripcion'])
        self.assertNotIn('883101', r['descripcion'])


class RecaudoValidadorSanitasTests(SimpleTestCase):
    """Caso 36888: dos pantallazos del validador (resonancia con Copago 11.5 %, sedación con Cuota Moderadora 0)."""
    TABLA = ("362023461 NORMAL 30/09/2026 28/01/2027 APROBADA NEUROELECTRODIAGNOSTICO\nSH DEL LLANO SAS\n"
             "883101 RESONANCIA\nMAGNETICA DE\nCEREBRO - 0.0 UVR\n"
             "362023956 NORMAL 30/09/2026 28/01/2027 APROBADA NEUROELECTRODIAGNOSTICO\nSH DEL LLANO SAS\n"
             "890375 CONSULTA DE\nCONTROL POR\n"
             "367288940 NORMAL 29/09/2026 27/01/2027 APROBADA NEUROELECTRODIAGNOSTICO\nSH DEL LLANO SAS\n"
             "998702 SOPORTE DE 1\nSEDACION PARA\nCONSULTA O APOYO\nDIAGNOSTICO - 0.0 UVR\n« ‹ 1 2 › »\n")
    TEXTO = (
        "[Página 1]\n" + TABLA + "Número de Autorización 362023461\nSeleccionada:\n"
        "Condiciones de pago y recuado de Copago\npago del Volante:\nhttps://appcore.colsanitas.com 1/2\n\n"
        "[Página 2]\n18/11/26, 7:54 Validador de usuarios Sanitas\nRecaudo de pago: 0.115\nPorcentaje Copago: 11.5\n"
        "Valor pesos pago compartido: 0.0\nTope máximo copagos: 373715.0\nInformación Complementaria\n\n"
        "[Página 3]\n" + TABLA + "Número de Autorización 367288940\nSeleccionada:\n"
        "Condiciones de pago y recuado de Cuota Moderadora\npago del Volante:\nhttps://appcore.colsanitas.com 1/2\n\n"
        "[Página 4]\n18/11/26, 7:54 Validador de usuarios Sanitas\nRecaudo de pago: 0\nPorcentaje Copago: 0.0\n"
        "Valor pesos pago compartido: 0.0\nTope máximo copagos: 0.0\n")

    def test_toma_la_autorizacion_del_cups_de_la_admision(self):
        from gedocumental.services.ai_revision import _recaudo_validador_sanitas
        resonancia = dict(ADMISION, cups=['883101 - RESONANCIA MAGNÉTICA DE CEREBRO (cant. 1)'])
        self.assertEqual(_recaudo_validador_sanitas(self.TEXTO, resonancia),
                         {'requiere_pago': 'SI', 'tipo_cobro': 'COPAGO', 'valor': '0', 'porcentaje': '11.5'})
        sedacion = dict(ADMISION, cups=['998702 - SOPORTE DE SEDACION (cant. 1)'])
        self.assertEqual(_recaudo_validador_sanitas(self.TEXTO, sedacion)['requiere_pago'], 'NO')
        # Ninguna autorización del CUPS de la admisión: se deja lo que leyó la IA
        self.assertIsNone(_recaudo_validador_sanitas(self.TEXTO, dict(ADMISION, cups=['879301 - TAC (cant. 1)'])))
        self.assertIsNone(_recaudo_validador_sanitas('Factura de venta', resonancia))

    def test_resonancia_y_sedacion_en_admisiones_separadas(self):
        # Regla 2026-11-19: estudio y sedación siempre van en dos admisiones; la autorización de la sedación en el
        # PDF de la resonancia no es hallazgo (los registrados se descartan) y el copago no se valida
        from gedocumental.services import recaudo
        resonancia = dict(ADMISION, cups=['883101 - RESONANCIA MAGNÉTICA DE CEREBRO (cant. 1)'])
        d = ("La autorización trae servicios que no están en la admisión: autorización 367288940 → 998702 "
             "(SOPORTE DE SEDACION PARA CONSULTA O APOYO DIAGNOSTICO), facturado en la admisión 36889. Se deben "
             "facturar en el mismo ingreso (ej. resonancia y sedación juntas).")
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', d, resonancia)[0], DESCARTADO)
        leido = {'requiere_pago': 'SI', 'tipo_cobro': 'COPAGO', 'valor': '0', 'porcentaje': '11.5'}
        with mock.patch.object(recaudo, 'con_sedacion', return_value=True), \
                mock.patch.object(recaudo, 'datos_recaudo_zeus') as zeus:
            self.assertIsNone(recaudo.verificar_recaudo(leido, 36888))
            zeus.assert_not_called()


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
        # Autorizado 8 y facturado 1 ya no es hallazgo (regla 2026-11-19); facturar más que lo autorizado sí
        self.assertEqual(self._llamar('930860:2; 891509:8')['tipo'], 'OK')
        self.ADM = dict(self.ADM, cups=[c.replace('(cant. 1)', '(cant. 9)') if c.startswith('891509') else c
                                        for c in self.ADM['cups']])
        r = self._llamar('930860:2; 891509:8')
        self.assertEqual(r['tipo'], 'AUTORIZACION')
        self.assertEqual(r['descripcion'], 'Cantidad autorizada en el documento es 8, se esperaba 9 para el '
                                           'procedimiento 891509.')
        # Se corrige en Zeus: la reverificación lo cierra; si no, sigue abierto
        corregida = dict(self.ADM, cups=[self.ADM['cups'][0].replace('(cant. 9)', '(cant. 8)'), self.ADM['cups'][1]])
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
        # Autorizado 8, facturado 1: facturar menos no es hallazgo; facturar más sí
        self.assertEqual(_cantidades_distintas_zeus('930860:2; 891509:8', self.ADM, '8'), [])
        self.assertEqual(_cantidades_distintas_zeus('954626:1', adm, ''), [('954626', 1, 2)])

    def test_dos_autorizaciones_con_anotaciones_distintas(self):
        # Caso 36927: tabla 1 en ambas, "CANTIDAD 8" (891509) y "CANTIDAD 2" (930860); Zeus 8 y 2. La IA leyó
        # solo una anotación (2): la relectura por autorización trae las dos y coinciden con Zeus
        import json
        from django.test import override_settings
        from gedocumental.services.ai_revision import _llamar_openai, _anotada_igual_zeus
        adm = dict(self.ADM, cups=['891509 - NEUROCONDUCCIÓN (CADA NERVIO) 8 NERVIOS (cant. 8)',
                                   '930860 - ELECTROMIOGRAFÍA EN CADA EXTREMIDAD (UNO O MÁS MÚSCULOS) (cant. 2)'])
        revision = {"nombre_documento": "ARNOLDO ROJAS", "identificacion_documento": "17313342",
                    "paciente_coincide": "SI", "procedimientos_coinciden": "SI", "firma_paciente": "NO_APLICA",
                    "tipo": "OK", "descripcion": "Documento correcto", "cantidades_documento": "891509:1; 930860:2",
                    "cantidad_anotada": "2"}
        def respuesta(contenido):
            return mock.MagicMock(choices=[mock.MagicMock(message=mock.MagicMock(content=json.dumps(contenido)))],
                                  usage=mock.MagicMock(prompt_tokens=100, completion_tokens=10))
        client = mock.MagicMock()
        client.chat.completions.create.side_effect = [
            respuesta(revision),
            respuesta({"anotaciones": [{"cups": "891509", "cantidad": "CANTIDAD 8"}, {"cups": "930860", "cantidad": "2"}]})]
        with override_settings(OPENAI_API_KEY='x'), mock.patch('openai.OpenAI', return_value=client):
            r = _llamar_openai('', ['img'], 'AUTORIZACION', adm)
        self.assertEqual(r['tipo'], 'OK')
        self.assertEqual(r['cantidad_anotada'], '891509:8; 930860:2')
        # Anotación de otra cantidad o sin la del CUPS del hallazgo: sigue
        d = 'Cantidad autorizada en el documento es 1, se esperaba 8 para el procedimiento 891509.'
        self.assertFalse(_anotada_igual_zeus('891509:6; 930860:2', adm, d))
        self.assertFalse(_anotada_igual_zeus('930860:2', adm, d))

    def test_tres_autorizaciones_respuesta_cortada(self):
        # Caso 36985: 891515 CANTIDAD 2, 930860 CANTIDAD 2, 891509 CANTIDAD 8 (tablas en 1); Zeus 2, 2 y 8
        from gedocumental.services.ai_revision import _releer_cantidad, _anotada_igual_zeus
        adm = dict(self.ADM, cups=['891509 - NEUROCONDUCCIÓN (CADA NERVIO) 8 NERVIOS (cant. 8)',
                                   '930860 - ELECTROMIOGRAFÍA EN CADA EXTREMIDAD (cant. 2)',
                                   '891515 - REFLEJO H (POR NERVIO) (cant. 2)'])
        cortada = ('{"anotaciones": [{"cups": "891515", "cantidad": "2"}, {"cups": "930860", "cantidad": "2"}, '
                   '{"cups": "891509", "cantidad": "8"}, {"cups": "8915')
        client = mock.MagicMock()
        client.chat.completions.create.return_value = mock.MagicMock(
            choices=[mock.MagicMock(message=mock.MagicMock(content=cortada))],
            usage=mock.MagicMock(prompt_tokens=100, completion_tokens=300))
        anotada, _ = _releer_cantidad(client, 'm', ['img'])
        self.assertEqual(anotada, '891515:2; 930860:2; 891509:8')
        d = ('Cantidad autorizada en el documento es 1, se esperaba 2 para el procedimiento 891515; Cantidad '
             'autorizada en el documento es 1, se esperaba 2 para el procedimiento 930860.')
        self.assertTrue(_anotada_igual_zeus(anotada, adm, d))

    def test_horas_son_la_cantidad(self):
        # Caso 37121: "MONITORIZACIÓN ELECTROENCEFALOGRAMA 12 HORAS" al pie; tabla 1; Zeus 891901-12 cant. 12
        import json
        from django.test import override_settings
        from gedocumental.services.ai_revision import _llamar_openai, _anotada_igual_zeus, _cantidad_anotada
        adm = dict(self.ADM, cups=['891901-12 - MONITORIZACIÓN ELECTROENCEFALOGRAFICA POR VIDEO Y RADIO (cant. 12)'])
        revision = {"nombre_documento": "ARNOLDO ROJAS", "identificacion_documento": "17313342",
                    "paciente_coincide": "SI", "procedimientos_coinciden": "SI", "firma_paciente": "NO_APLICA",
                    "tipo": "AUTORIZACION", "cantidad_anotada": "",
                    "descripcion": "Cantidad autorizada en el documento es 1, se esperaba 12 para el procedimiento 891901."}
        def respuesta(contenido):
            return mock.MagicMock(choices=[mock.MagicMock(message=mock.MagicMock(content=json.dumps(contenido)))],
                                  usage=mock.MagicMock(prompt_tokens=100, completion_tokens=10))
        client = mock.MagicMock()
        client.chat.completions.create.side_effect = [
            respuesta(revision), respuesta({"anotaciones": [{"cups": "891901", "cantidad": "12 HORAS"}]})]
        with override_settings(OPENAI_API_KEY='x'), mock.patch('openai.OpenAI', return_value=client):
            r = _llamar_openai('', ['img'], 'AUTORIZACION', adm)
        self.assertEqual(r['tipo'], 'OK')
        self.assertFalse(_anotada_igual_zeus('891901:8', adm, revision['descripcion']))
        # Servidor: la relectura general insiste en la tabla ('891901:1'); la pregunta por las horas trae 12
        adm2 = dict(self.ADM, cups=['891901 - MONITORIZACIÓN ELECTROENCEFALOGRAFICA POR VIDEO Y RADIO 12 HORAS (cant. 12)'])
        client = mock.MagicMock()
        client.chat.completions.create.side_effect = [
            respuesta(revision), respuesta({"anotaciones": [{"cups": "891901", "cantidad": "1"}]}),
            respuesta({"horas": "12"})]
        with override_settings(OPENAI_API_KEY='x'), mock.patch('openai.OpenAI', return_value=client):
            r = _llamar_openai('', ['img'], 'AUTORIZACION', adm2)
        self.assertEqual(r['tipo'], 'OK')
        self.assertEqual(r['cantidad_anotada'], '12 HORAS')
        # Otras horas: sigue el hallazgo
        client.chat.completions.create.side_effect = [
            respuesta(revision), respuesta({"anotaciones": [{"cups": "891901", "cantidad": "1"}]}),
            respuesta({"horas": "8"})]
        with override_settings(OPENAI_API_KEY='x'), mock.patch('openai.OpenAI', return_value=client):
            self.assertEqual(_llamar_openai('', ['img'], 'AUTORIZACION', adm2)['tipo'], 'AUTORIZACION')
        # PDF digital
        self.assertTrue(_cantidad_anotada('MONITORIZACION ELECTROENCEFALOGRAMA 12 HORAS', [12]))
        self.assertTrue(_cantidad_anotada('VIDEO EEG 8H', [8]))
        self.assertFalse(_cantidad_anotada('VALIDO HASTA 20261213 HORA 06:54', [12]))

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
        self.assertEqual((r['tipo'], llamadas), ('INFORMACION', 3))   # + ¿manuscrita?
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
        # Autorizado de más: no es hallazgo (regla 2026-11-19)
        self.assertEqual(_cantidades_distintas_zeus('873420:3', self.ADM), [])
        self.assertEqual(_cantidades_distintas_zeus('873420:2; 873422:1', self.ADM), [])

    def test_hallazgo_existente_se_cierra(self):
        desc = "Cantidad autorizada en el documento es 2, se esperaba 1 para el procedimiento 873420."
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', desc, self.ADM)[0], CORREGIDO)
        otra = dict(self.ADM, cups=['873420 - RADIOGRAFÍA DE RODILLA (AP- LATERAL) (cant. 1)',
                                    '873111 - RADIOGRAFÍA DE CRÁNEO (cant. 1)'])
        # Sin la familia: autorizado 2, facturado 1 → tampoco es hallazgo (regla 2026-11-19)
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', desc, otra)[0], DESCARTADO)


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
        # Zeus sigue con 1: autorizado de más, facturado menos → no es hallazgo (regla 2026-11-19)
        self.assertEqual(evaluar_hallazgo('AUTORIZACION', desc,
                                          dict(ADMISION, cups=['873420 - RADIOGRAFÍA DE RODILLA (cant. 1)']))[0],
                         DESCARTADO)


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
        self.assertIsNone(evaluar_hallazgo('AUTORIZACION', "Cantidad autorizada es 1, se esperaba 6.",
                                           dict(ADMISION, cups=['881302 - ECOGRAFÍA DE ABDOMEN TOTAL (cant. 6)'])))
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

    def test_historia_clinica_no_se_revisa(self):
        # Caso 35580: HC y adicionales no se revisan, ni siquiera el paciente
        desc = "Paciente no corresponde: documento 'HCLINICA', admisión '35580'."
        for tipo in ('HCLINICA', 'HCNEURO', 'ADICIONALES'):
            self.assertEqual(evaluar_hallazgo(tipo, desc, self.ADM)[0], DESCARTADO)
        from gedocumental.services.ai_revision import TIPOS_SIN_LLAMADA_IA
        self.assertIn('HCLINICA', TIPOS_SIN_LLAMADA_IA)
        self.assertIn('HCNEURO', TIPOS_SIN_LLAMADA_IA)


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
                mock.patch.object(reverificacion, 'cantidades_orden', return_value=''), \
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

    def test_cuota_moderadora_sanitas_mal_leida(self):
        # Caso 37167: validador escaneado "Recaudo de pago: 5000.0" leído como 600; SIESA cobró 5.000
        doc = {'requiere_pago': 'SI', 'tipo_cobro': 'CUOTA_MODERADORA', 'valor': '600.0', 'porcentaje': '0.0',
               'categoria': 'NO_INDICA', 'tipo_afiliado': 'COTIZANTE', 'exoneracion': 'NO'}
        zeus = dict(contrato='SANITAS EVENTO CONTRIBUTIVO', consultas=[True], cuota_moderadora=5000.0)
        self.assertIsNone(self._verificar(doc, **zeus))
        # Un valor válido distinto al cobrado sí es hallazgo
        self.assertIn('Valor recaudado distinto', self._verificar(dict(doc, valor='20100'), **zeus)[1])

    def test_valor_por_sesion(self):
        # Caso 35301 (Capital Salud): "PAGUE POR CADA SESION $29.800", 2 sesiones; se cobró $59.600
        doc = {'requiere_pago': 'SI', 'tipo_cobro': 'COPAGO', 'valor': '29.800'}
        zeus = dict(empresa='EPS025', contrato='CAPITAL SALUD CONTRIBUTIVO', copago=59600.0)
        self.assertIsNone(self._verificar(doc, sesiones=2, **zeus))
        # Con una sola sesión en la admisión, el doble sí es valor distinto
        self.assertEqual(self._verificar(doc, sesiones=1, **zeus)[0], 'RECAUDO')
        # Tres veces el valor con 2 sesiones tampoco
        self.assertEqual(self._verificar(doc, sesiones=2, **dict(zeus, copago=89400.0))[0], 'RECAUDO')

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


class EscaneoConTextoSinPacienteTests(SimpleTestCase):
    """Caso 11999: orden escaneada con capa de texto (OCR) sin el nombre: se revisa la imagen antes del hallazgo."""

    SIN_DATOS = {"tipo": "INFORMACION", "tokens": [10, 1], "llamadas": 1,
                 "descripcion": "Paciente no corresponde: el documento no contiene el nombre ni el documento de "
                                "identidad del paciente de la admisión (LAURA ESTEFANIA QUEVEDO ROCHA, CC 1122919525)."}
    OK = {"tipo": "OK", "descripcion": "Documento correcto", "tokens": [20, 2], "llamadas": 1}

    def _analizar(self, respuestas, imagenes=('img',)):
        from gedocumental.services import ai_revision
        with mock.patch.object(ai_revision, '_leer_documento', return_value=('texto OCR ' * 20, [])), \
                mock.patch.object(ai_revision, '_leer_pdf', return_value=('texto OCR ' * 20, list(imagenes))) as leer, \
                mock.patch.object(ai_revision, '_llamar_openai', side_effect=[dict(r) for r in respuestas]) as llamar:
            r = ai_revision.analizar_documento('/x/11999O.pdf', 'ORDEN', {'paciente': 'LAURA ESTEFANIA QUEVEDO ROCHA'})
        return r, leer, llamar

    def test_se_revisa_la_imagen_y_queda_ok(self):
        r, leer, llamar = self._analizar([self.SIN_DATOS, self.OK])
        self.assertEqual(r['tipo'], 'OK')
        self.assertEqual(r['modo'], 'mixto')
        self.assertEqual(r['llamadas'], 2)
        self.assertEqual(r['tokens'], [30, 3])
        self.assertEqual(llamar.call_args_list[1].args[1], ['img'])
        self.assertTrue(leer.call_args.kwargs['siempre_imagen'])

    def test_otro_hallazgo_no_repite(self):
        otro = dict(self.SIN_DATOS, descripcion='Falta el código CUPS')
        r, leer, llamar = self._analizar([otro])
        self.assertEqual(llamar.call_count, 1)
        leer.assert_not_called()
        self.assertEqual(r['modo'], 'texto')


class BilateralCantidadTests(SimpleTestCase):
    """Autorización con 'Tipo de intervención BILATERAL' y cantidad 1: cubre los 2 que factura la admisión."""
    DESC = "Cantidad autorizada en el documento es 1, se esperaba 2 para el procedimiento 954626."
    ADM = {'paciente': 'PACIENTE PRUEBA', 'cups': ['954626 - POTENCIALES EVOCADOS AUDITIVOS (cant. 2)'],
           'contrato': 'SANITAS EVENTO CONTRIBUTIVO'}
    TEXTO = "954626 POTENCIALES EVOCADOS AUDITIVOS DE CORTA LATENCIA BILATERAL 1"

    def test_bilateral_cubre_dos(self):
        from gedocumental.services.ai_revision import _bilateral_cubre
        self.assertTrue(_bilateral_cubre(self.DESC, self.TEXTO))
        self.assertTrue(_bilateral_cubre(self.DESC, '', {"estudio_documento": {"lateralidad": "BILATERAL"}}))

    def test_sin_bilateral_sigue_hallazgo(self):
        from gedocumental.services.ai_revision import _bilateral_cubre
        self.assertFalse(_bilateral_cubre(self.DESC, "954626 POTENCIALES EVOCADOS AUDITIVOS 1"))
        self.assertFalse(_bilateral_cubre(
            "Cantidad autorizada en el documento es 1, se esperaba 4 para el procedimiento 954626.", self.TEXTO))

    def test_reverificacion_descarta(self):
        r = evaluar_hallazgo('AUTORIZACION', self.DESC, self.ADM, texto=self.TEXTO)
        self.assertEqual(r[0], DESCARTADO)

    def test_escaneada_relee_bilateral(self):
        import json
        from django.test import override_settings
        from gedocumental.services.ai_revision import _llamar_openai
        revision = {"nombre_documento": "PACIENTE PRUEBA", "identificacion_documento": "1000000001",
                    "paciente_coincide": "SI", "procedimientos_coinciden": "SI", "firma_paciente": "NO_APLICA",
                    "tipo": "AUTORIZACION", "cantidad_anotada": "", "cantidades_documento": "954626:1",
                    "descripcion": self.DESC}

        def respuesta(contenido):
            return mock.MagicMock(choices=[mock.MagicMock(message=mock.MagicMock(content=json.dumps(contenido)))],
                                  usage=mock.MagicMock(prompt_tokens=100, completion_tokens=10))
        adm = dict(self.ADM, identificacion='1000000001')
        client = mock.MagicMock()
        client.chat.completions.create.side_effect = [respuesta(revision), respuesta({"procedimientos": [{"cups": "954626", "bilateral": "SI"}]})]
        with override_settings(OPENAI_API_KEY='x'), mock.patch('openai.OpenAI', return_value=client):
            r = _llamar_openai('', ['img'], 'AUTORIZACION', adm)
        self.assertEqual(r['tipo'], 'OK')
        # No dice bilateral: sigue la relectura de "CANTIDAD N" y el hallazgo
        client = mock.MagicMock()
        client.chat.completions.create.side_effect = [
            respuesta(revision), respuesta({"procedimientos": [{"cups": "954626", "bilateral": "NO"}]}),
            respuesta({"anotaciones": [{"cups": "954626", "cantidad": "1"}]})]
        with override_settings(OPENAI_API_KEY='x'), mock.patch('openai.OpenAI', return_value=client):
            self.assertEqual(_llamar_openai('', ['img'], 'AUTORIZACION', adm)['tipo'], 'AUTORIZACION')

    def test_escaneada_dos_autorizaciones_bilaterales(self):
        # Caso 37074: un PDF con dos autorizaciones (doppler venoso y arterial), cada una cantidad 1 y "BILATERAL"
        import json
        from django.test import override_settings
        from gedocumental.services.ai_revision import _llamar_openai
        desc = ("Cantidad autorizada en el documento es 1, se esperaba 2 para el procedimiento 882317; Cantidad "
                "autorizada en el documento es 1, se esperaba 2 para el procedimiento 882308.")
        revision = {"nombre_documento": "PACIENTE PRUEBA", "identificacion_documento": "1000000001",
                    "paciente_coincide": "SI", "procedimientos_coinciden": "SI", "firma_paciente": "NO_APLICA",
                    "tipo": "AUTORIZACION", "cantidad_anotada": "", "cantidades_documento": "882317:1; 882308:1",
                    "descripcion": desc}
        adm = dict(self.ADM, identificacion='1000000001',
                   cups=['882317 - ECOGRAFIA DOPPLER DE VASOS VENOSOS DE MIEMBROS INFERIORES (cant. 2)',
                         '882308 - ECOGRAFIA DOPPLER DE VASOS ARTERIALES DE MIEMBROS INFERIORES (cant. 2)'])

        def respuesta(contenido):
            return mock.MagicMock(choices=[mock.MagicMock(message=mock.MagicMock(content=json.dumps(contenido)))],
                                  usage=mock.MagicMock(prompt_tokens=100, completion_tokens=10))
        client = mock.MagicMock()
        client.chat.completions.create.side_effect = [respuesta(revision), respuesta({"procedimientos": [
            {"cups": "882317", "bilateral": "SI"}, {"cups": "882308", "bilateral": "SI"}]})]
        with override_settings(OPENAI_API_KEY='x'), mock.patch('openai.OpenAI', return_value=client):
            self.assertEqual(_llamar_openai('', ['img'], 'AUTORIZACION', adm)['tipo'], 'OK')
        # Solo una es bilateral: sigue el hallazgo
        client = mock.MagicMock()
        client.chat.completions.create.side_effect = [
            respuesta(revision), respuesta({"procedimientos": [
                {"cups": "882317", "bilateral": "SI"}, {"cups": "882308", "bilateral": "NO"}]}),
            respuesta({"anotaciones": [{"cups": "882317", "cantidad": "1"}, {"cups": "882308", "cantidad": "1"}]})]
        with override_settings(OPENAI_API_KEY='x'), mock.patch('openai.OpenAI', return_value=client):
            self.assertEqual(_llamar_openai('', ['img'], 'AUTORIZACION', adm)['tipo'], 'AUTORIZACION')

    def test_lateralidad_derecho_e_izquierdo(self):
        # Caso 37017: Capital Salud escribe "LATERALIDAD DERECHO E IZQUIERDO" en vez de "BILATERAL"
        from gedocumental.services.ai_revision import _bilateral_cubre, _es_bilateral
        desc = "Cantidad autorizada en el documento es 1, se esperaba 2 para el procedimiento 882318."
        self.assertTrue(_bilateral_cubre(desc, "882318 ECOGRAFIA DOPPLER 1\nLATERALIDAD DERECHO\nE IZQUIERDO"))
        self.assertTrue(_es_bilateral("Lateralidad: izquierda y derecha"))
        self.assertFalse(_es_bilateral("LATERALIDAD DERECHO"))


class OrdenRecienNacidoMamaTests(SimpleTestCase):
    """Caso 33358: orden del recién nacido a nombre de la mamá, que comparte apellido con el bebé."""
    ADM = {'paciente': 'ANA LUCIA PEREZ GOMEZ', 'apellidos': 'PEREZ GÓMEZ', 'identificacion': 'RC 1000000001',
           'fecha': '2026-09-20', 'fecha_nacimiento': '2026-09-10', 'cups': ['954626 - POTENCIALES (cant. 2)']}
    DESC = "Paciente no corresponde: documento 'MARIA ELENA GOMEZ RUIZ', admisión 'ANA LUCIA PEREZ GOMEZ'."

    def test_bebe_con_orden_de_la_mama(self):
        from gedocumental.services.ai_revision import _clasificar_paciente, _documento_de_la_mama
        self.assertTrue(_documento_de_la_mama('MARIA ELENA GOMEZ RUIZ', self.ADM))
        r = {'nombre_documento': 'MARIA ELENA GOMEZ RUIZ', 'identificacion_documento': '2000000002'}
        self.assertEqual(_clasificar_paciente(r, '', self.ADM), 'misma_persona')

    def test_sin_apellido_comun_o_mayor_de_un_anio(self):
        from gedocumental.services.ai_revision import _documento_de_la_mama
        self.assertFalse(_documento_de_la_mama('MARIA ELENA TORRES RUIZ', self.ADM))
        self.assertFalse(_documento_de_la_mama('MARIA ELENA GOMEZ RUIZ', dict(self.ADM, fecha_nacimiento='2020-01-01')))
        self.assertFalse(_documento_de_la_mama('MARIA ELENA GOMEZ RUIZ', dict(self.ADM, fecha_nacimiento='')))

    def test_reverificacion_descarta(self):
        r = evaluar_hallazgo('ORDEN', self.DESC, self.ADM)
        self.assertEqual(r[0], DESCARTADO)
        self.assertIn('mamá', r[1])


class NombreDelMedicoTests(SimpleTestCase):
    """Caso 34508: la IA tomó como paciente a la médica que firma la última hoja de la orden (R.M., especialidad)."""
    ADM = {'paciente': 'ROSA ELENA PARDO DIAZ', 'apellidos': 'PARDO DIAZ', 'identificacion': 'CC 1000000001',
           'fecha': '2026-10-01', 'fecha_nacimiento': '1970-01-01', 'cups': ['890274 - CONSULTA NEUROLOGIA (cant. 1)']}
    DESC = "Paciente no corresponde: documento 'MARTADANELA RUIZ OJEDA', admisión 'ROSA ELENA PARDO DIAZ'."

    def test_es_profesional_con_mala_lectura(self):
        from gedocumental.services.ai_revision import _es_profesional
        self.assertTrue(_es_profesional('MARTADANELA RUIZ OJEDA', ['MARTA DANIELA RUIZ OJEDA']))
        self.assertFalse(_es_profesional('LUIS CARLOS TORRES VEGA', ['MARTA DANIELA RUIZ OJEDA']))

    def test_pdf_digital_nombre_con_registro(self):
        from gedocumental.services.ai_revision import _nombre_de_profesional_en_texto, _clasificar_paciente
        texto = "MONITOREO HOLTER 1(Un)\nMARTA DANIELA RUIZ OJEDA\nCardiologia\nR.M. CC2000000002"
        self.assertTrue(_nombre_de_profesional_en_texto('MARTA DANIELA RUIZ OJEDA', texto))
        self.assertFalse(_nombre_de_profesional_en_texto('MARTA DANIELA RUIZ OJEDA',
                                                         "PACIENTE: MARTA DANIELA RUIZ OJEDA CC 2000000002"))
        r = {'nombre_documento': 'MARTA DANIELA RUIZ OJEDA', 'identificacion_documento': ''}
        self.assertEqual(_clasificar_paciente(r, texto, self.ADM), 'misma_persona')

    def _respuesta(self, contenido):
        import json
        return mock.MagicMock(choices=[mock.MagicMock(message=mock.MagicMock(content=json.dumps(contenido)))],
                              usage=mock.MagicMock(prompt_tokens=100, completion_tokens=10))

    def _revisar(self, relecturas):
        from django.test import override_settings
        from gedocumental.services.ai_revision import _llamar_openai
        revision = {"nombre_documento": "MARTADANELA RUIZ OJEDA", "identificacion_documento": "2000000002",
                    "paciente_coincide": "NO", "procedimientos_coinciden": "SI", "firma_paciente": "NO_APLICA",
                    "tipo": "INFORMACION", "descripcion": self.DESC}
        client = mock.MagicMock()
        client.chat.completions.create.side_effect = [self._respuesta(revision)] + [
            self._respuesta(r) for r in relecturas]
        with override_settings(OPENAI_API_KEY='x'), mock.patch('openai.OpenAI', return_value=client), \
                mock.patch('gedocumental.services.ai_revision._parte_superior', return_value=None):
            return _llamar_openai('', ['img'], 'ORDEN', self.ADM)

    def test_escaneo_solo_con_la_firma_del_medico(self):
        r = self._revisar([{"nombre": "", "identificaciones": ["2000000002"],
                            "profesionales": ["MARTA DANIELA RUIZ OJEDA"]}])
        self.assertEqual(r['tipo'], 'OK')

    def test_escaneo_de_otro_paciente_sigue_hallazgo(self):
        r = self._revisar([{"nombre": "LUIS CARLOS TORRES VEGA", "identificaciones": ["3000000003"],
                            "profesionales": ["MARTA DANIELA RUIZ OJEDA"]}])
        self.assertEqual(r['tipo'], 'INFORMACION')


class OrdenCantidadNoCorrespondeTests(SimpleTestCase):
    """Caso 37439: 'cantidad (2) no corresponde a la admisión (1)' en una ORDEN: pide más, no es error."""
    DESC = ("Nombre del usuario correcto. Procedimiento en documento (879420) coincide, pero cantidad (2) no "
            "corresponde a la admisión (1).")
    ADM = {'paciente': 'PACIENTE PRUEBA', 'cups': ['879420 - TOMOGRAFIA DE ABDOMEN Y PELVIS (cant. 1)']}

    def test_orden_con_mas_cantidad(self):
        from gedocumental.services.ai_revision import _orden_con_mas_cantidad, _solo_motivo_cantidad
        self.assertTrue(_solo_motivo_cantidad(self.DESC))
        self.assertTrue(_orden_con_mas_cantidad('ORDEN', self.DESC))
        self.assertEqual(evaluar_hallazgo('ORDEN', self.DESC, self.ADM)[0], DESCARTADO)

    def test_procedimiento_no_corresponde_sigue_siendo_otro_motivo(self):
        from gedocumental.services.ai_revision import _solo_motivo_cantidad
        self.assertFalse(_solo_motivo_cantidad("El procedimiento no corresponde; cantidad 2, se esperaba 1."))


class RegistrarRevisionSinDuplicadosTests(SimpleTestCase):
    """Revisiones simultáneas del mismo archivo no dejan varios hallazgos pendientes iguales (caso 38018)."""

    def _registrar(self, igual):
        from gedocumental.services import ai_revision
        archivo = mock.MagicMock(IdArchivo=1, Admision_id=99, Tipo='AUTORIZACION', Usuario_id=5)
        resultado = {"tipo": "RECAUDO", "descripcion": "No se recaudó; debía cobrarse copago de $1.000"}
        with mock.patch('gedocumental.models.RevisionIA.objects') as objetos:
            pendientes = objetos.filter.return_value
            pendientes.filter.return_value.order_by.return_value.first.return_value = igual
            ai_revision._registrar_revision_bloqueada(archivo, resultado, datetime(2026, 11, 24))
            return objetos, pendientes

    def test_mismo_hallazgo_pendiente_no_se_duplica(self):
        objetos, pendientes = self._registrar(mock.MagicMock(pk=7))
        objetos.create.assert_not_called()
        pendientes.exclude.assert_called_once_with(pk=7)

    def test_hallazgo_nuevo_se_crea(self):
        objetos, pendientes = self._registrar(None)
        objetos.create.assert_called_once()
        pendientes.update.assert_called_once()
