"""Pruebas de la auditoría de honorarios (sin base de datos)."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from controlfacturacion.services import honorarios as h


def _tarifa(**k):
    base = dict(cups='930860', grupo='TODOS', tipo='FIJO', valor=Decimal('22000'), porcentaje=None,
                max_puntos=None, escalas='', por_unidad=False, solo_contrato='')
    base.update(k)
    return SimpleNamespace(**base)


MEDICOS = [
    {'codigo': 6, 'nombre': 'PAOLA ANDREA NAVARRO', 'especialidad': 'RADIOLOGIA'},
    {'codigo': 7, 'nombre': 'MARIA LIGIA RIVERO NOCHES', 'especialidad': 'RADIOLOGIA'},
    {'codigo': 14, 'nombre': 'MAIRA ALEJANDRA FORERO', 'especialidad': 'MEDICINA FISICA Y REHABILITACION'},
    {'codigo': 21, 'nombre': 'ANA ELEONORA ROJAS ROJAS', 'especialidad': 'GINECOLOGIA'},
    {'codigo': 49, 'nombre': 'ANA MERCEDES MACHADO PALACIOS', 'especialidad': 'ENFERMERIA EN SALUD FAMILIAR'},
    {'codigo': 60, 'nombre': 'JHON EDINSON MOSQUERA', 'especialidad': 'NEUROLOGIA'},
    {'codigo': 68, 'nombre': 'KAREN HELENA RIZCALA', 'especialidad': 'NEUROLOGIA'},
    {'codigo': 9, 'nombre': 'ISMAEL ALFONSO MARTINEZ FLECHAS', 'especialidad': 'RADIOLOGIA'},
]


class PeriodoTests(SimpleTestCase):
    def test_mes_vencido(self):
        self.assertEqual(h.periodo_anterior(date(2026, 11, 18)), '2026-10')
        self.assertEqual(h.periodo_anterior(date(2027, 1, 5)), '2026-12')

    def test_rango(self):
        self.assertEqual(h.rango_periodo('2026-12'), (date(2026, 12, 1), date(2027, 1, 1)))


class CupsYContratoTests(SimpleTestCase):
    def test_normaliza_ceros_y_sufijo(self):
        self.assertEqual(h.norm_cups('053105'), '53105')
        self.assertEqual(h.norm_cups('053105-10'), '53105-10')
        self.assertEqual(h.cups_base('891509-8'), '891509')
        self.assertEqual(h.norm_cups(891402), '891402')

    def test_excluidos(self):
        for c in ('992990', '998701', '890226'):
            self.assertTrue(h.excluido(c))
        self.assertFalse(h.excluido('998702'))   # sedación de anestesia (escalonado) sí entra

    def test_grupo_sanitas_solo_evento_y_mrc(self):
        self.assertEqual(h.grupo_contrato('SANITAS EVENTO CONTRIBUTIVO'), 'SANITAS')
        self.assertEqual(h.grupo_contrato('SANITAS MRC SUBSIDIADO'), 'SANITAS')
        self.assertEqual(h.grupo_contrato('COLSANITAS PLAN MODULAR'), 'ENTIDADES')
        self.assertEqual(h.grupo_contrato('MEDISANITAS'), 'ENTIDADES')
        self.assertEqual(h.grupo_contrato('PARTICULAR 20% DESCUENTO'), 'PARTICULAR')
        self.assertEqual(h.grupo_contrato('CAPITAL SALUD SUBSIDIADO'), 'ENTIDADES')


class ValorExcelTests(SimpleTestCase):
    def test_tipos(self):
        self.assertEqual(h.interpretar_valor(21000), {'tipo': 'FIJO', 'valor': Decimal('21000')})
        self.assertEqual(h.interpretar_valor('70% del valor total de la fra')['porcentaje'], Decimal('70'))
        punto = h.interpretar_valor('40.000                    ( x punto)')
        self.assertEqual((punto['tipo'], punto['valor']), ('POR_PUNTO', Decimal('40000')))
        maximo = h.interpretar_valor('Max. 4 puntos a c/u 15000')
        self.assertEqual((maximo['valor'], maximo['max_puntos']), (Decimal('15000'), 4))
        self.assertIsNone(h.interpretar_valor('#REF!'))
        self.assertIsNone(h.interpretar_valor(None))


class AsociarMedicoTests(SimpleTestCase):
    def test_nombre_de_pila(self):
        self.assertEqual(h.asociar_medico('PAOLA', MEDICOS), 6)
        self.assertEqual(h.asociar_medico('MAIRA', MEDICOS), 14)          # no se confunde con MARIA
        self.assertEqual(h.asociar_medico('ANA', MEDICOS), 21)            # la enfermera no cuenta

    def test_errores_de_digitacion_y_orden(self):
        self.assertEqual(h.asociar_medico('JHON EDISON MOSQUERA', MEDICOS), 60)
        self.assertEqual(h.asociar_medico('KAREN RISCALIA', MEDICOS), 68)
        self.assertEqual(h.asociar_medico('NAVARRO GUTIERREZ PAOLA ANDREA', MEDICOS), 6)   # Lumier
        self.assertEqual(h.asociar_medico('ALFONSO   MARTINEZ FLECHAS', MEDICOS), 9)
        self.assertEqual(h.asociar_medico('SOFY (HORA)', MEDICOS), None)


class TarifaYCalculoTests(SimpleTestCase):
    def test_por_unidad_multiplica_cantidad(self):
        t = _tarifa(cups='891509', valor=Decimal('34125'), por_unidad=True)
        self.assertEqual(h.calcular_honorario(t, 8, 0)[0], Decimal('273000'))

    def test_por_estudio_avisa_cantidad(self):
        valor, alertas = h.calcular_honorario(_tarifa(valor=Decimal('23295')), 12, 0)
        self.assertEqual(valor, Decimal('23295'))
        self.assertTrue(any('Cantidad 12' in a for a in alertas))

    def test_porcentaje_del_valor_facturado(self):
        t = _tarifa(tipo='PORCENTAJE', valor=None, porcentaje=Decimal('65'))
        self.assertEqual(h.calcular_honorario(t, 1, Decimal('200000'))[0], Decimal('130000'))

    def test_por_punto_con_maximo(self):
        t = _tarifa(tipo='POR_PUNTO', valor=Decimal('15000'), max_puntos=4, por_unidad=True)
        valor, alertas = h.calcular_honorario(t, 10, 0)
        self.assertEqual(valor, Decimal('60000'))
        self.assertTrue(alertas)

    def test_escalonado(self):
        t = _tarifa(tipo='ESCALONADO', valor=None, escalas='400000,500000,600000')
        self.assertEqual(h.calcular_honorario(t, 1, 0, procedimientos_admision=2)[0], Decimal('500000'))
        self.assertEqual(h.calcular_honorario(t, 1, 0, procedimientos_admision=5)[0], Decimal('600000'))

    def test_eleccion_por_grupo(self):
        tarifas = [_tarifa(grupo='ENTIDADES', valor=Decimal('33075')), _tarifa(grupo='SANITAS', valor=Decimal('25305'))]
        self.assertEqual(h.elegir_tarifa(tarifas, '930860', 'SANITAS MRC CONTRIBUTIVO')[0].valor, Decimal('25305'))
        self.assertEqual(h.elegir_tarifa(tarifas, '930860', 'CAPITAL SALUD')[0].valor, Decimal('33075'))
        t, alertas = h.elegir_tarifa(tarifas, '930860', 'PARTICULAR TARIFA PLENA')
        self.assertEqual(t.valor, Decimal('33075'))
        self.assertTrue(any('particular' in a for a in alertas))

    def test_sufijo_pediatrico_y_base(self):
        tarifas = [_tarifa(cups='891402', valor=Decimal('17878')), _tarifa(cups='891402-1', valor=Decimal('26000'))]
        self.assertEqual(h.elegir_tarifa(tarifas, '891402-1', 'X')[0].valor, Decimal('26000'))
        self.assertEqual(h.elegir_tarifa([_tarifa(cups='891509')], '891509-8', 'X')[0].cups, '891509')

    def test_solo_contrato(self):
        tarifas = [_tarifa(cups='879201', solo_contrato='IPS CONSULTORIO')]
        self.assertIsNotNone(h.elegir_tarifa(tarifas, '879201', 'IPS CONSULTORIO MEDICO SALUD OCUPACIONAL')[0])
        self.assertIsNone(h.elegir_tarifa(tarifas, '879201', 'CAPITAL SALUD')[0])


class CruceLumierTests(SimpleTestCase):
    def _e(self, pk, cups, fecha, doc='123', cod=6):
        return SimpleNamespace(pk=pk, documento=doc, cups=cups, fecha=fecha, cod_medico=cod)

    def test_cantidad_consume_varias_filas_y_otro_cups(self):
        lineas = [
            {'sis_deta_id': 1, 'documento': '123', 'cod_servicio': '879201', 'fecha': date(2026, 7, 1), 'cantidad': 2},
            {'sis_deta_id': 2, 'documento': '123', 'cod_servicio': '873431', 'fecha': date(2026, 7, 4), 'cantidad': 1},
        ]
        estudios = [self._e(10, '879201', date(2026, 7, 1)), self._e(11, '879201', date(2026, 7, 2)),
                    self._e(12, '873333', date(2026, 7, 4)), self._e(13, '883101', date(2026, 7, 20))]
        pareja, otro, sin = h.cruzar_lumier(lineas, estudios)
        self.assertEqual([e.pk for e in pareja[1]], [10, 11])
        self.assertEqual([e.pk for e in otro[2]], [12])
        self.assertEqual([e.pk for e in sin], [13])


class ReglasNoviembreTests(SimpleTestCase):
    """Reglas 2026-11-21: por cantidad, doppler, agenda por horas y glosa parcial."""

    def test_monitorizacion_potenciales_e_infiltraciones_por_cantidad(self):
        t = _tarifa(cups='891901', valor=Decimal('23295'), descripcion='MONITORIZACION ELECTROENCEFALOGRAFICA')
        valor, alertas = h.calcular_honorario(t, 12, 0, descripcion='MONITORIZACIÓN ... 12 HORAS')
        self.assertEqual(valor, Decimal('279540'))
        self.assertFalse(alertas)
        pot = _tarifa(cups='954626', valor=Decimal('69000'), descripcion='POTENCIALES EVOCADOS AUDITIVOS')
        self.assertEqual(h.calcular_honorario(pot, 2, 0)[0], Decimal('138000'))
        inf = _tarifa(cups='861401', valor=Decimal('30000'), descripcion='INFILTRACION INTRALESIONAL')
        self.assertEqual(h.calcular_honorario(inf, 3, 0)[0], Decimal('90000'))
        # En particular la tarifa es por estudio (potenciales de Wilder: $150.000)
        part = _tarifa(cups='952303', valor=Decimal('150000'), grupo='PARTICULAR', descripcion='POTENCIALES VISUALES')
        self.assertEqual(h.calcular_honorario(part, 2, 0)[0], Decimal('150000'))

    def test_doppler_por_cantidad(self):
        tarifas = [
            _tarifa(cups='882308', valor=Decimal('64437'), descripcion='ECOGRAFIA DOPPLER DE VASOS ARTERIALES DE MIEMBROS INFERIORES'),
            _tarifa(cups='882308', valor=Decimal('80000'), descripcion='ECOGRAFIA DOPPLER DE VASOS ARTERIALES DE MIEMBROS INFERIORES - BILATERAL'),
            _tarifa(cups='882317', valor=Decimal('80000'), descripcion='ECOGRAFIA DOPPLER DE VASOS VENOSOS DE MIEMBROS INFERIORES - BILATERAL'),
        ]
        uno, alertas = h.elegir_tarifa(tarifas, '882308', 'CAPITAL SALUD', 1)
        self.assertEqual(uno.valor, Decimal('64437'))
        self.assertFalse(alertas)
        dos = h.elegir_tarifa(tarifas, '882308', 'CAPITAL SALUD', 2)[0]
        self.assertEqual(dos.valor, Decimal('80000'))
        self.assertEqual(h.calcular_honorario(dos, 2, 0), (Decimal('80000'), []))
        # 882317 solo trae la fila bilateral: con cantidad 1 se paga la unilateral del médico
        self.assertEqual(h.elegir_tarifa(tarifas, '882317', 'X', 1)[0].valor, Decimal('64437'))

    def test_tarifa_con_sufijo_y_linea_sin_el(self):
        t = h.elegir_tarifa([_tarifa(cups='891402-1', valor=Decimal('26000'))], '891402', 'X')[0]
        self.assertEqual(t.valor, Decimal('26000'))

    def test_paquete_particular_por_cups_de_zeus(self):
        tarifas = [_tarifa(cups='5', grupo='PARTICULAR', valor=Decimal('180000'))]
        self.assertEqual(h.elegir_tarifa(tarifas, '000005', 'PARTICULAR TARIFA PLENA')[0].valor, Decimal('180000'))

    def test_medico_por_hora(self):
        self.assertTrue(h.es_tarifa_hora(_tarifa(encabezado='SOFY (HORA)')))
        self.assertFalse(h.es_tarifa_hora(_tarifa(encabezado='SEBASTIAN POSADA')))

    def test_horas_de_agenda(self):
        bloques = [{'dia': date(2026, 10, 13), 'inicio': '07:30', 'fin': '11:30', 'intervalo': 30, 'cupos': 9},
                   {'dia': date(2026, 10, 13), 'inicio': '13:00', 'fin': '15:30', 'intervalo': 30, 'cupos': 6},
                   {'dia': date(2026, 10, 10), 'inicio': '07:00', 'fin': '11:30', 'intervalo': 30, 'cupos': 10}]
        dias = h.horas_agenda(bloques)
        self.assertEqual([d['dia'].day for d in dias], [10, 13])
        self.assertEqual(dias[0]['horas'], Decimal(5))
        self.assertEqual(dias[0]['bloques'][0]['fin'], '12:00')
        self.assertEqual(dias[1]['horas'], Decimal('7.5'))
        self.assertLess(h.id_agenda(date(2026, 10, 13), 17), 0)
        self.assertNotEqual(h.id_agenda(date(2026, 10, 13), 17), h.id_agenda(date(2026, 10, 13), 13))

    def test_glosa_parcial(self):
        self.assertEqual(h.honorario_final(Decimal('280000'), 'GLOSA', None, Decimal('35000')), Decimal('245000'))
        self.assertEqual(h.honorario_final(Decimal('280000'), 'GLOSA'), Decimal(0))
        self.assertEqual(h.honorario_final(Decimal('280000'), 'OK', Decimal('250000')), Decimal('250000'))
        self.assertEqual(h.honorario_final(Decimal('100'), 'GLOSA', None, Decimal('500')), Decimal(0))
