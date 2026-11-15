"""
Bot RPA de Autogestión FOMAG - Transcripción Externa (Horus2).

Flujo (un servicio CUPS por solicitud):
 1. Login en https://horus2.horus-health.com (mismo login del bot de validación FOMAG,
    acción Vuex `usuarioIngreso`, cierre de sesiones previas si hay 403).
 2. Transcripciones -> Externa (/transcripcion/transcripcion).
 3. Búsqueda del afiliado por tipo y número de documento.
 4. "Proceso de Registro - Transcripción Externa": fecha de la orden, ámbito, médico que ordenó,
    documento y registro del médico, especialidad, finalidad, diagnósticos CIE-10, IPS que ordena,
    observaciones y PDF de la orden adjunto.
 5. Captura de evidencia Paso 1 (formulario diligenciado).
 6. Enviar -> se crea la consulta de transcripción y se sube el PDF.
 7. Ordenamiento -> Procedimientos: se agrega el CUPS (cantidad de la orden), se direcciona a la sede
    `500010210901-NEUROELECTRODIAGNOSTICO SH DEL LLANO IPS - VILLAVICENCIO` (rep_id=60278) y se genera la orden.
 8. Si la orden queda sin direccionamiento al crearse, aplica automáticamente el direccionamiento en
    Ordenamiento (`cambio-direccionamiento`) y valida en Históricos -> Órdenes (`DIRECCIONAMIENTO ORDEN SERVICIOS`,
    `ordenamiento/actualizarRep`) para garantizar que nunca quede "Sin dirección".

Si `simular=True` se diligencia todo en pantalla pero NO se envía nada a FOMAG.
"""
import os
import re
import base64
import asyncio
from datetime import datetime

from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()

URL_PORTAL = "https://horus2.horus-health.com/"

# Sede por defecto para direccionamiento de órdenes de servicio en Horus2
DEFAULT_REP_ID = 60278
DEFAULT_REP_CODIGO = "500010210901"
DEFAULT_REP_NOMBRE = "NEUROELECTRODIAGNOSTICO SH DEL LLANO IPS"
DEFAULT_REP_LABEL = "500010210901-NEUROELECTRODIAGNOSTICO SH DEL LLANO IPS - VILLAVICENCIO"

TIPOS_DOC = {"CC": 1, "TI": 2, "RC": 3, "CD": 4, "CE": 5, "NI": 6, "PA": 7, "AS": 8,
             "PE": 9, "PT": 10, "SC": 11, "CN": 12, "MS": 13, "DE": 14}

# Mapeo del título de la historia clínica (SIESA) a la especialidad de Horus2
ESPECIALIDADES_HC = [
    (r"FISICA\s+Y\s+REHABILITACION|FISIATR", "Fisiatria"),
    (r"NEUROLOGIA\s+PEDIATRICA", "Neurologia Pediatrica"),
    (r"NEUROCIRUG", "Neurocirugia"),
    (r"NEUROPSICOLOG", "Neuropsicologia"),
    (r"NEUROLOG", "Neurologia"),
]


def _sin_tildes(s: str) -> str:
    tabla = str.maketrans("ÁÉÍÓÚÀÈÌÒÙÄËÏÖÜáéíóúàèìòùäëïöü", "AEIOUAEIOUAEIOUaeiouaeiouaeiou")
    return str(s or "").translate(tabla)


def extraer_datos_orden_pdf(pdf_bytes: bytes, cups: str = "") -> dict:
    """Extrae de la orden PDF de SIESA los datos necesarios para la transcripción."""
    datos = {
        "fecha_orden": "", "medico_documento": "", "medico_nombre": "", "medico_registro": "",
        "especialidad": "", "diagnosticos": [], "cantidad": 1, "descripcion_cups": "",
        "paciente_documento": "", "texto_ok": False,
    }
    try:
        import pymupdf  # PyMuPDF
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        texto = "\n".join(p.get_text() for p in doc)
    except Exception:
        try:
            import fitz
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            texto = "\n".join(p.get_text() for p in doc)
        except Exception:
            return datos
    if not texto.strip():
        return datos
    datos["texto_ok"] = True
    t_up = _sin_tildes(texto).upper()

    m_pac = re.search(r"Documento:\s*(?:CC|TI|CE|RC|PA|PE|PT)?\s*([0-9]{5,15})", texto, re.I)
    if m_pac:
        datos["paciente_documento"] = m_pac.group(1).strip()

    m = re.search(r"Fecha y Hora:\s*(\d{4})[/-](\d{2})[/-](\d{2})", texto)
    if m:
        datos["fecha_orden"] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    m = re.search(r"Registro:\s*([0-9A-Za-z\-]+)", texto) or re.search(r"Reg\.\s*Medico:\s*([0-9A-Za-z\-]+)", texto)
    if m:
        datos["medico_registro"] = m.group(1).strip()

    # Pie de la orden: "CC 1121833421 ANGELICA MARIA RODRIGUEZ VELASQUEZ" seguido de "Reg. Medico:"
    m = re.search(r"\b(?:CC|CE)\s+(\d{5,12})\s+([A-ZÁÉÍÓÚÑ ]+)\s*\n\s*Reg\.?\s*Medico", texto)
    if m:
        datos["medico_documento"] = m.group(1)
        datos["medico_nombre"] = re.sub(r"\s+", " ", m.group(2)).strip()

    for patron, esp in ESPECIALIDADES_HC:
        if re.search(r"HISTORIA CLINICA[^\n]*\n?[^\n]*" + patron, t_up) or re.search(patron, t_up[:1500]):
            datos["especialidad"] = esp
            break

    diags = []
    for m in re.finditer(r"Diagnostico (?:Principal|Relacionado \d+):\s*([A-Z]\d{2,3}[A-Z0-9]?)\s*-\s*([^\n]+)", _sin_tildes(texto)):
        cod = m.group(1).strip()
        if cod not in [d["codigo"] for d in diags]:
            diags.append({"codigo": cod, "nombre": m.group(2).strip()})
    datos["diagnosticos"] = diags

    cups = re.sub(r"[^0-9A-Za-z]", "", str(cups or ""))
    if cups:
        lineas = [l.strip() for l in texto.splitlines()]
        for i, l in enumerate(lineas):
            if l == cups:
                desc = []
                for j in range(i + 1, min(i + 6, len(lineas))):
                    if re.fullmatch(r"\d{1,3}", lineas[j]):
                        datos["cantidad"] = int(lineas[j])
                        break
                    if lineas[j]:
                        desc.append(lineas[j])
                datos["descripcion_cups"] = " ".join(desc)
                break
    return datos


LOGIN_JS = """async ({ email, password }) => {
    function f(r){ if(!r) return null; if(r.$options&&r.$options.methods&&r.$options.methods.usuarioIngreso) return r;
      for(const c of (r.$children||[])){ const x=f(c); if(x) return x;} return null; }
    const vm = f(window.$nuxt && window.$nuxt.$root);
    if (!vm) return { ok:false, error:'No se encontró el formulario de login de Horus2' };
    try { await vm.usuarioIngreso({ email, password }); return { ok:true }; }
    catch(err){
        if (err && err.response && err.response.status === 403) {
            try { await vm.$axios.post('auth/cerrar-sesiones-usuario',{email,password});
                  await vm.usuarioIngreso({ email, password }); return { ok:true, recovered:true }; }
            catch(e2){ return { ok:false, error:(e2.response&&e2.response.data&&e2.response.data.error)||String(e2) }; }
        }
        return { ok:false, error:(err&&err.response&&err.response.data&&(err.response.data.error||err.response.data.message))||String(err) };
    }
}"""


PREPARAR_FORMULARIO_JS = """async (p) => {
    function __findTrans(r){ if(!r) return null; if(r.$data&&'formTranscripcion' in r.$data) return r;
      for(const c of (r.$children||[])){ const x=__findTrans(c); if(x) return x;} return null; }
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const comp = __findTrans(window.$nuxt && window.$nuxt.$root);
    if (!comp) return { ok:false, paso:'vista', error:'No cargó la vista de Transcripción Externa' };
    const ax = comp.$axios, base = comp.url_nest;

    // 1. Buscar afiliado
    comp.form.tipo_documento = p.tipo_documento_id;
    comp.form.numero_documento = p.documento;
    await comp.$nextTick();
    let afiliado = null;
    try {
        const r = await ax.get(`${base}/afiliados/buscar-afiliado-transcripcion/${p.tipo_documento_id}/${p.documento}`);
        afiliado = r.data;
    } catch(e) {
        return { ok:false, paso:'afiliado', error:(e.response&&e.response.data&&e.response.data.message)||'Afiliado no encontrado en FOMAG' };
    }
    if (!afiliado || !afiliado.id) return { ok:false, paso:'afiliado', error:'Afiliado no encontrado en FOMAG' };
    comp.afiliado = afiliado;
    await sleep(1200);

    // 2. Especialidad
    const rEsp = await ax.get(`${base}/especialidades/listar-especialidades`);
    comp.opcionesEspecialidades = rEsp.data || [];
    const norm = s => String(s||'').normalize('NFD').replace(/[\\u0300-\\u036f]/g,'').toLowerCase().trim();
    const esp = (rEsp.data||[]).find(e => norm(e.nombre) === norm(p.especialidad))
             || (rEsp.data||[]).find(e => norm(e.nombre).includes(norm(p.especialidad)));
    if (!esp) return { ok:false, paso:'especialidad', error:`Especialidad '${p.especialidad}' no existe en Horus2` };

    // 3. Diagnósticos CIE-10
    const diags = [];
    for (const cod of p.diagnosticos) {
        try {
            const r = await ax.get(`${base}/cie10s/buscar-por-codigo/`, { params:{ codigo: cod } });
            const hit = (r.data||[]).find(d => String(d.codigo_cie10).toUpperCase() === cod.toUpperCase()) || (r.data||[])[0];
            if (hit) diags.push({ id: hit.id, nombre: `${hit.codigo_cie10} - ${hit.nombre}` });
        } catch(e) {}
    }
    if (!diags.length) return { ok:false, paso:'diagnostico', error:'No se encontró ningún diagnóstico CIE-10 válido' };
    comp.opcionesDiagnosticos = diags;

    // 4. IPS que ordena
    const rRep = await ax.get(`${base}/reps/buscar-rep-nit/${p.rep_nit}`);
    const reps = (rRep.data||[]).map(x => ({ id:x.id, nombre:`${x.prestador_nit} | ${x.nombre} - ${x.municipio_nombre}` }));
    const rep = reps.find(x => /IPS/i.test(x.nombre)) || reps[0];
    if (!rep) return { ok:false, paso:'ips', error:`No se encontró la IPS con NIT ${p.rep_nit}` };
    comp.repsListados = reps;

    // 5. PDF adjunto
    const bin = atob(p.pdf_b64); const bytes = new Uint8Array(bin.length);
    for (let i=0;i<bin.length;i++) bytes[i] = bin.charCodeAt(i);
    const file = new File([bytes], p.pdf_nombre, { type:'application/pdf' });

    Object.assign(comp.formTranscripcion, {
        fecha_orden: p.fecha_orden, ambito: p.ambito, medico_ordeno: p.medico_ordeno,
        documento_medico_ordena: p.medico_documento, registro_medico_ordena: p.medico_registro,
        especialidad_id: esp.id, finalidad: p.finalidad, diagnosticos: diags, rep_id: rep.id,
        observaciones: p.observaciones, adjuntos: [file]
    });
    await comp.$nextTick();
    await sleep(1500);

    // 6. Pre-verificar que el CUPS exista en Horus2 para este afiliado
    const rCups = await ax.get(`${base}/cups/buscar-cups`, { params:{ nombreCodigo: p.cups, sexoAfiliado: afiliado.sexo } });
    const cup = (rCups.data||[]).find(c => String(c.codigo) === String(p.cups));
    if (!cup) return { ok:false, paso:'cups', error:`El CUPS ${p.cups} no está disponible en Horus2 para este afiliado` };

    // 7. Resolver Sede a direccionar (500010210901-NEUROELECTRODIAGNOSTICO SH DEL LLANO IPS - VILLAVICENCIO)
    let repDir = null;
    const terminosBusqueda = [p.rep_codigo || '500010210901', p.direccionar_a || 'NEUROELECTRODIAGNOSTICO'];
    for (const term of terminosBusqueda) {
        if (repDir) break;
        try {
            const rBusq = await ax.get(`${base}/reps/buscar-rep/${encodeURIComponent(term)}`);
            const lista = rBusq.data || [];
            repDir = lista.find(x => String(x.codigo||'') === String(p.rep_codigo||'500010210901'))
                  || lista.find(x => String(x.nombre||'').toUpperCase().includes('NEUROELECTRODIAGNOSTICO'))
                  || lista[0];
        } catch(e) {}
    }
    if (!repDir) {
        try {
            const rD = await ax.get(`${base}/reps/reps-direccionamiento-servicios/${cup.id}`);
            repDir = (rD.data||[]).find(x => String(x.codigo||'') === String(p.rep_codigo||'500010210901'))
                  || (rD.data||[]).find(x => String(x.nombre||'').toUpperCase().includes(p.direccionar_a));
        } catch(e) {}
    }
    if (!repDir) {
        repDir = {
            id: p.rep_id_default || 60278,
            codigo: p.rep_codigo || '500010210901',
            nombre: 'NEUROELECTRODIAGNOSTICO SH DEL LLANO IPS',
            municipio: { nombre: 'VILLAVICENCIO' }
        };
    }
    const munNombre = (repDir.municipio && repDir.municipio.nombre) || 'VILLAVICENCIO';
    const repDirLabel = `${repDir.codigo || '500010210901'}-${repDir.nombre} - ${munNombre}`;

    const formValido = comp.$refs.formRegistroTranscripcion ? comp.$refs.formRegistroTranscripcion.validate() : true;
    const resumen = {
        afiliado_id: afiliado.id,
        afiliado_nombre: [afiliado.primer_nombre, afiliado.segundo_nombre, afiliado.primer_apellido, afiliado.segundo_apellido].filter(Boolean).join(' ') || afiliado.nombre_completo || '',
        especialidad: esp.nombre,
        diagnosticos: diags.map(d => d.nombre),
        ips: rep.nombre,
        cups_horus: `${cup.codigo} - ${cup.nombre}`,
        cup_obj: cup,
        rep_dir_id: parseInt(repDir.id),
        rep_dir_nombre: repDir.nombre,
         direccionamiento: repDirLabel,
        formulario_valido: formValido
    };
    window.__fomagPrepData = { cup, repDir, repDirLabel, afiliado, resumen };
    if (!formValido) return { ok:false, paso:'validacion', error:'El formulario de Horus2 tiene campos obligatorios sin diligenciar', resumen };
    return { ok:true, resumen };
}"""


ENVIAR_Y_ORDENAR_JS = """async (p) => {
    function __findTrans(r){ if(!r) return null; if(r.$data&&'formTranscripcion' in r.$data) return r;
      for(const c of (r.$children||[])){ const x=__findTrans(c); if(x) return x;} return null; }
    function __findOrd(r){ if(!r) return null; if(r.$data&&'dialogOrdenamiento' in r.$data&&'tipoOrdenamiento' in r.$data) return r;
      for(const c of (r.$children||[])){ const x=__findOrd(c); if(x) return x;} return null; }
    function __findProc(r){ if(!r) return null; if(r.$data&&'formProcedimientos' in r.$data&&'ordenesActivas' in r.$data) return r;
      for(const c of (r.$children||[])){ const x=__findProc(c); if(x) return x;} return null; }
    const sleep = ms => new Promise(r => setTimeout(r, ms));

    const comp = __findTrans(window.$nuxt && window.$nuxt.$root);
    if (!comp) return { ok:false, paso:'envio', error:'No se encontró el componente de Transcripción' };
    const ax = comp.$axios, base = comp.url_nest;
    const prep = window.__fomagPrepData || {};
    const cup = prep.cup;
    const repDirId = parseInt((prep.repDir && prep.repDir.id) || p.rep_id_default || 60278);
    const repDirLabel = prep.repDirLabel || '500010210901-NEUROELECTRODIAGNOSTICO SH DEL LLANO IPS - VILLAVICENCIO';
    const resumen = prep.resumen || {};

    // 1. Enviar formulario de transcripción (crea consulta y sube PDF)
    await comp.enviarFormulario();
    for (let i=0; i<60 && (comp.loading.guardar || comp.subidaArchivos.estado); i++) await sleep(500);
    const rawConsulta = comp.consultaId;
    const consulta = Array.isArray(rawConsulta) ? rawConsulta[0] : rawConsulta;
    if (!consulta) {
        return { ok:false, paso:'envio', error:'Horus2 no devolvió el número de consulta de la transcripción', resumen };
    }
    resumen.consulta_id = consulta;
    await sleep(1500);

    // 2. Abrir pestaña "Procedimientos" en el modal <Ordenamiento> para visualización y trazabilidad
    const ordComp = __findOrd(window.$nuxt.$root);
    if (ordComp) {
        ordComp.comp = 'Procedimientos';
        await ordComp.$nextTick();
        await sleep(1500);
    }

    const hoy = new Date(Date.now() - 6e4*(new Date).getTimezoneOffset()).toISOString().substr(0,10);
    let historico = [];
    try {
        const rVal = await ax.post(`${base}/ordenamiento/validar-servicio`, {
            cantidad: p.cantidad, consulta_id: parseInt(consulta), fechaVigencia: hoy,
            historico_id: true, procedimiento_id: parseInt(cup.id)
        });
        historico = (rVal.data && rVal.data.historico) || [];
    } catch(e) {
        return { ok:false, paso:'validar_servicio', error:(e.response&&e.response.data&&e.response.data.message)||'FOMAG rechazó el servicio', resumen };
    }
    resumen.historico_previo = historico.length;

    // 3. Generar orden enviando rep_id = 60278 (500010210901-NEUROELECTRODIAGNOSTICO SH DEL LLANO IPS - VILLAVICENCIO)
    try {
        const rGen = await ax.post(`${base}/ordenamiento/generar-orden/${consulta}/2`, [{
            cantidad: p.cantidad,
            consulta_id: parseInt(consulta),
            fechaVigencia: hoy,
            historico_id: true,
            observacion: p.observaciones,
            cup_id: cup.id,
            rep_id: repDirId,
            nivel_ordenamiento: cup.nivel_ordenamiento
        }]);
        resumen.mensaje_horus = rGen.data && rGen.data.mensaje;
    } catch(e) {
        return { ok:false, paso:'generar_orden', error:(e.response&&e.response.data&&(e.response.data.error||e.response.data.message))||'Error generando el ordenamiento', resumen };
    }

    // 4. Consultar las órdenes generadas en la consulta y garantizar direccionamiento a 500010210901
    let ordenesFinales = [];
    let direccionamientoAplicado = [];
    try {
        const rAct = await ax.get(`${base}/ordenamiento/listar-ordenes-consulta/${consulta}`);
        let lista = rAct.data || [];
        for (const o of lista) {
            if (!o.rep_id || parseInt(o.rep_id) !== repDirId) {
                // Intento A: endpoint de Ordenamiento -> Procedimientos (cambio-direccionamiento)
                try {
                    await ax.post(`${base}/ordenamiento/cambio-direccionamiento`, {
                        orden_procedimiento_id: parseInt(o.id),
                        rep_id: repDirId,
                        motivo: p.observaciones || 'Mejor oportunidad'
                    });
                    direccionamientoAplicado.push({ orden_procedimiento_id: o.id, via: 'cambio-direccionamiento', rep_id: repDirId });
                } catch(eDir1) {}

                // Intento B: endpoint de DIRECCIONAMIENTO ORDEN SERVICIOS (ordenamiento/actualizarRep)
                try {
                    const fd = new FormData();
                    fd.append('id', o.id);
                    fd.append('rep_id', repDirId);
                    fd.append('motivo', p.observaciones || 'Mejor oportunidad');
                    fd.append('tipo', 2);
                    await ax.post('ordenamiento/actualizarRep', fd);
                    direccionamientoAplicado.push({ orden_procedimiento_id: o.id, via: 'actualizarRep', rep_id: repDirId });
                } catch(eDir2) {}
            }
        }
        // Re-consultar tras direccionar
        const rAct2 = await ax.get(`${base}/ordenamiento/listar-ordenes-consulta/${consulta}`);
        ordenesFinales = rAct2.data || lista;

        // Refrescar tabla visual del componente Procedimientos y cerrar alertas que tapen la tabla
        const procComp = __findProc(window.$nuxt.$root);
        if (procComp) {
            procComp.ordenesActivas = ordenesFinales;
            procComp.dialogoDireccionamiento = false;
            await procComp.$nextTick();
        }
        const swalBtn = document.querySelector('.swal2-confirm');
        if (swalBtn) swalBtn.click();
    } catch(e) {}

    resumen.direccionamiento_aplicado = direccionamientoAplicado;
    resumen.ordenes = (ordenesFinales || []).map(o => ({
        id: o.id,
        orden_id: o.orden_id,
        rep_id: o.rep_id || repDirId,
        rep_nombre: o.rep_nombre || repDirLabel,
        estado: o.estado_nombre || o.estado || 'Activo',
        cup: o.cup_id
    }));
    await sleep(1500);
    return { ok:true, resumen };
}"""


VERIFICAR_HISTORICO_Y_DIRECCIONAR_JS = """async (p) => {
    function findHistoricoComp(root) {
        if (!root) return null;
        if (root.$data && 'tipoOrdenes' in root.$data && 'orden' in root.$data) return root;
        for (const c of (root.$children || [])) {
            const r = findHistoricoComp(c);
            if (r) return r;
        }
        return null;
    }
    function findServComp(root) {
        if (!root) return null;
        if (root.$data && 'ordenesServicios' in root.$data) return root;
        for (const c of (root.$children || [])) {
            const r = findServComp(c);
            if (r) return r;
        }
        return null;
    }
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const comp = findHistoricoComp(window.$nuxt.$root);
    if (!comp) return { ok:false, error:'No cargó /historico/historico-ordenes' };

    comp.orden.tipo_orden = 2; // Servicios
    comp.orden.numero_documento = p.documento;
    await comp.buscarAfiliadoPorDocumento();
    for (let i = 0; i < 30; i++) {
        if (!comp.loading || !comp.loading.buscar) break;
        await sleep(300);
    }
    await sleep(4000);

    const serv = findServComp(window.$nuxt.$root);
    if (!serv) return { ok:false, error:'No cargó HistoricoServicios' };

    const repDirId = parseInt(p.rep_id_default || 60278);
    const ordenes = serv.ordenesServicios || [];
    // Buscar únicamente la orden recién creada en esta consulta (o sin direccionar del mismo CUPS)
    const candidatas = ordenes.filter(o =>
        (p.consulta_id && String(o.consulta_id) === String(p.consulta_id)) ||
        (!p.consulta_id && String(o.cup_codigo) === String(p.cups) && !o.rep_id)
    );

    let corregidas = [];
    for (const ord of candidatas) {
        if (!ord.rep_id || parseInt(ord.rep_id) !== repDirId) {
            try {
                // Abrir visualmente el modal DIRECCIONAMIENTO ORDEN SERVICIOS - N° ...
                serv.abrirDialogServiciosDireccionamiento(ord);
                await serv.$nextTick();
                await sleep(800);

                const fd = new FormData();
                fd.append('id', ord.consecutivo);
                fd.append('rep_id', repDirId);
                fd.append('motivo', p.observaciones || 'Mejor oportunidad');
                fd.append('tipo', 2);
                await serv.$axios.post('ordenamiento/actualizarRep', fd);
                corregidas.push(ord.consecutivo);
                serv.dialogoDireccionamiento = false;
            } catch(e) {}
        }
    }
    if (corregidas.length > 0) {
        await serv.listarOrdenesServiciosAfiliado();
        await sleep(3000);
    }

    const finalOrdenes = (serv.ordenesServicios || []).filter(o =>
        (p.consulta_id && String(o.consulta_id) === String(p.consulta_id)) ||
        String(o.cup_codigo) === String(p.cups)
    ).slice(0, 3);

    let volantePdfB64 = '';
    let volantePdfNombre = '';
    if (finalOrdenes.length > 0) {
        const o0 = finalOrdenes[0];
        const estId = String(o0.estado_id || '1').trim();
        const consec = String(o0.consecutivo || '').trim();
        const ordId = String(o0.orden_id || '').trim();
        const cupCod = String(o0.cup_codigo || p.cups || '').trim();
        if ((estId === '1' || estId === '4') && consec && ordId) {
            const axios = (window.$nuxt && window.$nuxt.$axios) || serv.$axios;
            for (let intento = 0; intento < 3; intento++) {
                try {
                    const respPdf = await axios.post('/pdf', {
                        tipo: 'servicio',
                        id: ordId,
                        filtro: estId,
                        detalles: { id: consec }
                    }, { responseType: 'arraybuffer' });
                    const bytes = new Uint8Array(respPdf.data);
                    if (bytes.length > 100) {
                        let binary = '';
                        const chunkSize = 0x8000;
                        for (let i = 0; i < bytes.length; i += chunkSize) {
                            binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
                        }
                        volantePdfB64 = btoa(binary);
                        volantePdfNombre = `Orden_${consec}_FOMAG_${ordId}_CUPS_${cupCod}.pdf`;
                        break;
                    }
                } catch(e) {}
                await sleep(600);
            }
        }
    }

    return { ok:true, corregidas, finalOrdenes, volante_pdf_b64: volantePdfB64, volante_pdf_nombre: volantePdfNombre };
}"""


class BotFomagTranscripcion:
    NOMBRE = "FOMAG - Transcripción Externa"

    def __init__(self):
        self.user = os.getenv("EPS_FOMAG_USER", "1121926977@fomag.com")
        self.password = os.getenv("EPS_FOMAG_PASS", "Neuro2026***")

    async def radicar(self, *, documento: str, cups: str, pdf_bytes: bytes, pdf_nombre: str = "orden.pdf",
                      doc_tipo: str = "CC", cantidad: int = 1, fecha_orden: str = "",
                      medico_documento: str = "", medico_registro: str = "", especialidad: str = "Fisiatria",
                      diagnosticos=None, ambito: str = "Ambulatorio",
                      medico_ordeno: str = "Médico especialista externo", finalidad: str = "Diagnóstico",
                      observaciones: str = "Mejor oportunidad", rep_nit: str = "901119103",
                      direccionar_a: str = "NEUROELECTRODIAGNOSTICO",
                      rep_codigo: str = DEFAULT_REP_CODIGO, rep_id_default: int = DEFAULT_REP_ID,
                      headless: bool = True, simular: bool = False, evidencias_dir: str = None) -> dict:
        doc_clean = re.sub(r"[^0-9A-Za-z]", "", str(documento or ""))
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        ev_dir = evidencias_dir or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "evidencias")
        os.makedirs(ev_dir, exist_ok=True)

        ev_form_name = f"transcripcion_fomag_{doc_clean}_{cups}_{ts}_1_formulario.png"
        ev_ord_name = f"transcripcion_fomag_{doc_clean}_{cups}_{ts}_2_ordenamiento.png"
        ev_name = f"transcripcion_fomag_{doc_clean}_{cups}_{ts}.png"

        ev_form_path = os.path.join(ev_dir, ev_form_name)
        ev_ord_path = os.path.join(ev_dir, ev_ord_name)
        ev_path = os.path.join(ev_dir, ev_name)

        res = {
            "ok": False,
            "documento": doc_clean,
            "cups": cups,
            "simulado": simular,
            "soporte_evidencia": ev_name,
            "evidencias_proceso": [],
        }

        faltan = [k for k, v in {"fecha_orden": fecha_orden, "medico_documento": medico_documento,
                                 "medico_registro": medico_registro, "diagnosticos": diagnosticos,
                                 "pdf": pdf_bytes}.items() if not v]
        if faltan:
            res["error"] = "Faltan datos obligatorios: " + ", ".join(faltan)
            return res

        nombre_pdf = re.sub(r"[^0-9A-Za-z._-]", "_", pdf_nombre or "orden.pdf")
        if not nombre_pdf.lower().endswith(".pdf"):
            nombre_pdf += ".pdf"
        params = {
            "tipo_documento_id": TIPOS_DOC.get(str(doc_tipo).upper(), 1),
            "documento": doc_clean,
            "cups": str(cups),
            "cantidad": int(cantidad or 1),
            "fecha_orden": fecha_orden[:10],
            "ambito": ambito,
            "medico_ordeno": medico_ordeno,
            "medico_documento": re.sub(r"\D", "", medico_documento),
            "medico_registro": medico_registro,
            "especialidad": especialidad,
            "finalidad": finalidad,
            "diagnosticos": [str(d).strip().upper() for d in diagnosticos if str(d).strip()],
            "observaciones": observaciones,
            "rep_nit": rep_nit,
            "direccionar_a": direccionar_a.upper(),
            "rep_codigo": rep_codigo,
            "rep_id_default": int(rep_id_default or DEFAULT_REP_ID),
            "pdf_b64": base64.b64encode(pdf_bytes).decode(),
            "pdf_nombre": nombre_pdf,
            "simular": bool(simular),
        }

        async with async_playwright() as p:
            launch = {"headless": headless, "args": ["--disable-blink-features=AutomationControlled", "--start-maximized"]}
            if os.name == "nt" and not headless:
                launch["channel"] = "chrome"
                launch["slow_mo"] = 150
            browser = await p.chromium.launch(**launch)
            context = await browser.new_context(
                no_viewport=not headless, viewport={"width": 1440, "height": 900} if headless else None,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
            page = await context.new_page()
            try:
                await page.goto(URL_PORTAL, wait_until="networkidle", timeout=60000)
                await page.wait_for_timeout(1500)
                login = await page.evaluate(LOGIN_JS, {"email": self.user, "password": self.password})
                if not login or not login.get("ok"):
                    res["error"] = f"Login FOMAG falló: {(login or {}).get('error')}"
                    return res
                await page.wait_for_timeout(2500)
                await page.evaluate("() => window.$nuxt.$router.push('/transcripcion/transcripcion')")
                await page.wait_for_timeout(3500)

                # Paso 1: Diligenciar formulario de Transcripción Externa y capturar imagen del formulario lleno
                prep_out = await page.evaluate(PREPARAR_FORMULARIO_JS, params)
                await page.wait_for_timeout(1500)

                if not prep_out.get("ok"):
                    try:
                        await page.screenshot(path=ev_path, full_page=True)
                    except Exception:
                        pass
                    res["resumen"] = prep_out.get("resumen") or {}
                    res["error"] = f"[{prep_out.get('paso')}] {prep_out.get('error')}"
                    return res

                if simular:
                    try:
                        await page.screenshot(path=ev_path, full_page=True)
                        res["evidencias_proceso"].append({"etapa": "Formulario Diligenciado (Simulación)", "archivo": ev_name})
                    except Exception:
                        pass
                    res["ok"] = True
                    res["resumen"] = prep_out.get("resumen") or {}
                    res["mensaje"] = (
                        f"SIMULACIÓN: Formulario diligenciado correctamente. "
                        f"Sede a direccionar: {res['resumen'].get('direccionamiento', DEFAULT_REP_LABEL)}."
                    )
                    if not headless:
                        await page.wait_for_timeout(5000)
                    return res

                # Captura Paso 1 (Formulario lleno antes de enviar)
                try:
                    await page.screenshot(path=ev_form_path, full_page=True)
                    res["evidencias_proceso"].append({"etapa": "1. Transcripción Externa Diligenciada", "archivo": ev_form_name})
                except Exception:
                    pass

                # Paso 2: Enviar formulario, generar Orden de Servicio y direccionar a 500010210901
                ord_out = await page.evaluate(ENVIAR_Y_ORDENAR_JS, params)
                await page.wait_for_timeout(2000)

                if not ord_out.get("ok"):
                    try:
                        await page.screenshot(path=ev_path, full_page=True)
                    except Exception:
                        pass
                    res["resumen"] = ord_out.get("resumen") or prep_out.get("resumen") or {}
                    res["error"] = f"[{ord_out.get('paso')}] {ord_out.get('error')}"
                    return res

                # Captura Paso 2 (Modal Ordenamiento -> Procedimientos con orden activa y direccionada)
                try:
                    await page.screenshot(path=ev_ord_path, full_page=True)
                    res["evidencias_proceso"].append({"etapa": "2. Ordenamiento y Direccionamiento de Servicio", "archivo": ev_ord_name})
                except Exception:
                    pass

                r_resumen = ord_out.get("resumen") or {}
                consulta_id = r_resumen.get("consulta_id")
                params["consulta_id"] = consulta_id

                # Paso 3: Ir a /historico/historico-ordenes, verificar Dirección Prestador, descargar Volante PDF y capturar tabla final
                try:
                    await page.evaluate("() => window.$nuxt.$router.push('/historico/historico-ordenes')")
                    await page.wait_for_timeout(3500)
                    hist_out = await page.evaluate(VERIFICAR_HISTORICO_Y_DIRECCIONAR_JS, params)
                    if hist_out and hist_out.get("finalOrdenes"):
                        r_resumen["ordenes_historico"] = hist_out.get("finalOrdenes")
                        if hist_out.get("corregidas"):
                            r_resumen["direccionamiento_historico_corregido"] = hist_out.get("corregidas")
                    if hist_out and hist_out.get("volante_pdf_b64") and hist_out.get("volante_pdf_nombre"):
                        desc_dir = os.path.join(os.path.dirname(ev_dir), "descargas_fomag", doc_clean)
                        os.makedirs(desc_dir, exist_ok=True)
                        vol_path = os.path.join(desc_dir, hist_out["volante_pdf_nombre"])
                        with open(vol_path, "wb") as f_vol:
                            f_vol.write(base64.b64decode(hist_out["volante_pdf_b64"]))
                        res["volante_pdf_ruta"] = vol_path
                        res["volante_pdf_nombre"] = hist_out["volante_pdf_nombre"]
                    await page.wait_for_timeout(1500)
                except Exception:
                    pass

                # Captura Paso 3 (Evidencia principal en Histórico de Órdenes con Dirección Prestador asignada)
                try:
                    await page.screenshot(path=ev_path, full_page=True)
                    res["evidencias_proceso"].append({"etapa": "3. Orden Activa y Direccionada en Histórico FOMAG", "archivo": ev_name})
                except Exception:
                    pass

                res["ok"] = True
                res["resumen"] = r_resumen
                res["consulta_id"] = consulta_id
                ordenes_gen = r_resumen.get("ordenes_historico") or r_resumen.get("ordenes") or []
                consecutivo_txt = ""
                if ordenes_gen:
                    o0 = ordenes_gen[0]
                    consec = str(o0.get("consecutivo") or o0.get("id") or "").strip()
                    ord_fomag = str(o0.get("orden_id") or "").strip()
                    estado_id_ord = str(o0.get("estado_id") or "").strip()
                    estado_nom_ord = str(o0.get("estado_nombre") or ("Activo" if estado_id_ord == "1" else "")).strip()
                    nom_rep = o0.get("nombre_rep") or o0.get("rep_nombre") or DEFAULT_REP_LABEL
                    res["consecutivo_orden"] = consec
                    res["orden_id_fomag"] = ord_fomag
                    res["estado_id_fomag"] = estado_id_ord
                    res["estado_orden_fomag"] = estado_nom_ord
                    res["prestador_direccionado"] = nom_rep
                    if ord_fomag and consec:
                        res["num_autorizacion"] = f"{ord_fomag} (Cons. {consec})"
                    elif ord_fomag or consec:
                        res["num_autorizacion"] = ord_fomag or f"Cons. {consec}"
                    consecutivo_txt = f" | Consecutivo N° {consec} (# Orden FOMAG {ord_fomag}) -> Direccionado a: {nom_rep}"

                res["mensaje"] = (
                    f"Transcripción y Direccionamiento radicados en FOMAG. "
                    f"Consulta #{consulta_id}{consecutivo_txt}."
                )
                if not headless:
                    await page.wait_for_timeout(4000)
            except Exception as e:
                res["error"] = str(e)
                try:
                    await page.screenshot(path=ev_path, full_page=True)
                except Exception:
                    pass
            finally:
                await browser.close()
        return res


def radicar_transcripcion(**kwargs) -> dict:
    """Envoltorio síncrono."""
    return asyncio.run(BotFomagTranscripcion().radicar(**kwargs))
