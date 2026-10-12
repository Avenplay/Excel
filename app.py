import streamlit as st
import pandas as pd
from datetime import datetime
from PIL import Image
import json
import psycopg2
from psycopg2 import IntegrityError
import warnings
from google import genai

warnings.filterwarnings('ignore', 'pandas only supports SQLAlchemy connectable')

CATEGORIAS = [
    "Alimentación",
    "Limpieza e Higiene",
    "Hogar y Equipamiento",
    "Ocio y Salidas",
    "Jardinería",
    "Videojuegos y Tech",
    "Coche y Movilidad",
    "Otros / Varios"
]

# --- SEGURIDAD ---
def check_password():
    def password_entered():
        if st.session_state["password"] == st.secrets.get("APP_PASSWORD", ""):
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.text_input("Introduce la contraseña para acceder:", type="password", on_change=password_entered, key="password")
        return False
    elif not st.session_state["password_correct"]:
        st.text_input("Contraseña incorrecta. Inténtalo de nuevo:", type="password", on_change=password_entered, key="password")
        return False
    return True

if not check_password():
    st.stop()

st.set_page_config(page_title="Gestor de Finanzas y Previsiones", layout="wide")

# --- CONEXIÓN Y BASE DE DATOS ---
def init_connection():
    return psycopg2.connect(st.secrets["DATABASE_URL"])

def inicializar_base_datos():
    conexion = init_connection()
    conexion.autocommit = True
    cursor = conexion.cursor()
    
    # 1. Movimientos contables
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS movimientos_caja (
        id SERIAL PRIMARY KEY,
        fecha DATE NOT NULL,
        tipo TEXT NOT NULL,               -- 'Gasto', 'Ingreso Fijo', 'Ingreso Extra'
        categoria TEXT NOT NULL,          -- Categoría del gasto o 'Nómina'/'Extra'
        concepto TEXT NOT NULL,
        monto NUMERIC(10,2) NOT NULL,
        metodo_pago TEXT NOT NULL,        -- 'Tarjeta/PayPal', 'Efectivo'
        subcuenta_extra TEXT DEFAULT 'N/A'
    )""")
    
    # Migración en caliente: crea las columnas nuevas si la tabla ya existía de antes
    cursor.execute("ALTER TABLE movimientos_caja ADD COLUMN IF NOT EXISTS tipo TEXT DEFAULT 'Gasto'")
    cursor.execute("ALTER TABLE movimientos_caja ADD COLUMN IF NOT EXISTS categoria TEXT DEFAULT 'Otros / Varios'")
    
    # 2. Gastos fijos automáticos mensuales
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS gastos_recurrentes (
        id SERIAL PRIMARY KEY,
        nombre_gasto TEXT NOT NULL UNIQUE,
        monto NUMERIC(10,2) NOT NULL
    )""")
    
    # 3. Financiaciones
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS compras_plazos (
        id SERIAL PRIMARY KEY,
        articulo TEXT NOT NULL UNIQUE,
        monto_total NUMERIC(10,2) NOT NULL,
        meses_totales INTEGER NOT NULL,
        meses_pagados INTEGER NOT NULL DEFAULT 0
    )""")
    
    # 4. Huchas para pagos anuales
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS previsiones_anuales (
        id SERIAL PRIMARY KEY,
        concepto TEXT NOT NULL UNIQUE,
        monto_total NUMERIC(10,2) NOT NULL,
        mes_objetivo INTEGER NOT NULL
    )""")
    
    # 5. Proyectos y metas
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS proyectos_futuros (
        id SERIAL PRIMARY KEY,
        nombre_proyecto TEXT NOT NULL UNIQUE,
        objetivo_total NUMERIC(10,2) NOT NULL,
        meses_restantes INTEGER NOT NULL,
        ahorrado_acumulado NUMERIC(10,2) NOT NULL DEFAULT 0.00
    )""")
    
    # 6. Configuración global
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS config_sistema (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        fondo_emergencia NUMERIC(10,2) NOT NULL DEFAULT 0.00,
        nomina_estimada NUMERIC(10,2) NOT NULL DEFAULT 1300.00
    )""")
    cursor.execute("INSERT INTO config_sistema (id, fondo_emergencia, nomina_estimada) VALUES (1, 0.0, 1300.0) ON CONFLICT (id) DO NOTHING")
    
    conexion.close()

def ejecutar_automatizaciones():
    mes_actual = datetime.now().strftime("%Y-%m")
    conexion = init_connection()
    cursor = conexion.cursor()
    
    # Fijos recurrentes
    cursor.execute("SELECT nombre_gasto, monto FROM gastos_recurrentes")
    for nombre, monto in cursor.fetchall():
        concepto_cargo = f"Fijo Automático: {nombre}"
        cursor.execute(
            "SELECT COUNT(*) FROM movimientos_caja WHERE concepto = %s AND fecha::text LIKE %s", 
            (concepto_cargo, f"{mes_actual}%")
        )
        if cursor.fetchone()[0] == 0:
            cursor.execute(
                "INSERT INTO movimientos_caja (fecha, tipo, categoria, concepto, monto, metodo_pago) VALUES (%s, 'Gasto', 'Fijo Recurrente', %s, %s, 'Tarjeta/PayPal')", 
                (datetime.now().date(), concepto_cargo, monto)
            )
            
    # Compras a plazos
    cursor.execute("SELECT id, articulo, monto_total, meses_totales, meses_pagados FROM compras_plazos WHERE meses_pagados < meses_totales")
    for pid, articulo, total, m_totales, m_pagados in cursor.fetchall():
        concepto_base = f"Cuota Plazo: {articulo} (%%"
        cursor.execute(
            "SELECT COUNT(*) FROM movimientos_caja WHERE concepto LIKE %s AND fecha::text LIKE %s", 
            (concepto_base, f"{mes_actual}%")
        )
        if cursor.fetchone()[0] == 0:
            concepto_cuota = f"Cuota Plazo: {articulo} ({m_pagados + 1}/{m_totales})"
            cuota = round(float(total) / m_totales, 2)
            cursor.execute(
                "INSERT INTO movimientos_caja (fecha, tipo, categoria, concepto, monto, metodo_pago) VALUES (%s, 'Gasto', 'Financiación', %s, %s, 'Tarjeta/PayPal')", 
                (datetime.now().date(), concepto_cuota, cuota)
            )
            cursor.execute("UPDATE compras_plazos SET meses_pagados = meses_pagados + 1 WHERE id = %s", (pid,))
            
    conexion.commit()
    conexion.close()

@st.cache_resource
def arrancar_sistema():
    inicializar_base_datos()
    ejecutar_automatizaciones()
    return True

arrancar_sistema()

# --- MENÚ LATERAL ---
st.sidebar.title("📌 Menú Principal")
opcion = st.sidebar.radio("Ir a:", [
    "💵 Control de Caja",
    "📝 Plantilla de Gastos (Sin Ticket)",
    "📷 Lector de Tickets IA",
    "🔮 Previsiones y Ahorro",
    "🔄 Gastos Fijos y Plazos",
    "🗓️ Provisiones Anuales",
    "📊 Análisis y Estadísticas",
    "⚙️ Configuración"
])

# ==========================================
# 1. CONTROL DE CAJA
# ==========================================
if opcion == "💵 Control de Caja":
    st.title("💵 Control de Caja y Balances")
    conexion = init_connection()
    cursor = conexion.cursor()
    
    cursor.execute("""
        SELECT 
            COALESCE(SUM(CASE WHEN tipo='Ingreso Fijo' THEN monto ELSE 0 END), 0) +
            COALESCE(SUM(CASE WHEN subcuenta_extra='Extra-Banco' THEN monto ELSE 0 END), 0) -
            COALESCE(SUM(CASE WHEN tipo='Gasto' AND metodo_pago='Tarjeta/PayPal' THEN monto ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN subcuenta_extra='Extra-Efectivo' THEN monto ELSE 0 END), 0) -
            COALESCE(SUM(CASE WHEN tipo='Gasto' AND metodo_pago='Efectivo' THEN monto ELSE 0 END), 0)
        FROM movimientos_caja
    """)
    saldo_banco, saldo_efectivo = cursor.fetchone()
    
    cursor.execute("SELECT COALESCE(SUM(monto_total / 12.0), 0) FROM previsiones_anuales")
    colchon_mes = cursor.fetchone()[0]
    
    cursor.execute("SELECT fondo_emergencia FROM config_sistema WHERE id=1")
    fondo_emerg = cursor.fetchone()[0]
    
    df_movs = pd.read_sql_query("SELECT * FROM movimientos_caja ORDER BY fecha DESC, id DESC", conexion)
    conexion.close()
    
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("💳 Bolsa Banco", f"{float(saldo_banco):,.2f} €")
    c2.metric("💵 Hucha Efectivo", f"{float(saldo_efectivo):,.2f} €")
    c3.metric("🛡️ Provisiones/Mes", f"{float(colchon_mes):,.2f} €")
    c4.metric("🛟 Fondo Emergencia", f"{float(fondo_emerg):,.2f} €")
    
    st.markdown("---")
    
    with st.expander("➕ Registrar Entrada de Dinero (Nómina o Extra)"):
        with st.form("form_ingreso", clear_on_submit=True):
            ci1, ci2, ci3 = st.columns(3)
            with ci1: concepto_ing = st.text_input("Concepto (ej. Nómina, Venta Wallapop)")
            with ci2: monto_ing = st.number_input("Importe (€)", min_value=1.0, step=50.0)
            with ci3: tipo_ing = st.selectbox("Tipo de Entrada", ["Ingreso Fijo (Banco)", "Extra a Banco", "Extra a Efectivo"])
            
            if st.form_submit_button("Guardar Entrada") and concepto_ing:
                con_w = init_connection()
                cur_w = con_w.cursor()
                hoy = datetime.now().date()
                if tipo_ing == "Ingreso Fijo (Banco)":
                    cur_w.execute("INSERT INTO movimientos_caja (fecha, tipo, categoria, concepto, monto, metodo_pago, subcuenta_extra) VALUES (%s, 'Ingreso Fijo', 'Nómina', %s, %s, 'Tarjeta/PayPal', 'N/A')", (hoy, concepto_ing, monto_ing))
                elif tipo_ing == "Extra a Banco":
                    cur_w.execute("INSERT INTO movimientos_caja (fecha, tipo, categoria, concepto, monto, metodo_pago, subcuenta_extra) VALUES (%s, 'Ingreso Extra', 'Extra', %s, %s, 'Tarjeta/PayPal', 'Extra-Banco')", (hoy, concepto_ing, monto_ing))
                else:
                    cur_w.execute("INSERT INTO movimientos_caja (fecha, tipo, categoria, concepto, monto, metodo_pago, subcuenta_extra) VALUES (%s, 'Ingreso Extra', 'Extra', %s, %s, 'Efectivo', 'Extra-Efectivo')", (hoy, concepto_ing, monto_ing))
                con_w.commit()
                con_w.close()
                st.rerun()

    st.subheader("📜 Historial Reciente")
    if not df_movs.empty:
        for _, row in df_movs.head(20).iterrows():
            col_t, col_b = st.columns([9, 1])
            with col_t:
                signo = "+" if "Ingreso" in row['tipo'] else "-"
                st.write(f"📅 **{row['fecha']}** | `{row['categoria']}` | **{row['concepto']}** | **{signo}{float(row['monto']):,.2f} €** ({row['metodo_pago']})")
            with col_b:
                if st.button("🗑️", key=f"del_m_{row['id']}"):
                    con_d = init_connection()
                    cur_d = con_d.cursor()
                    cur_d.execute("DELETE FROM movimientos_caja WHERE id=%s", (row['id'],))
                    con_d.commit()
                    con_d.close()
                    st.rerun()
            st.markdown("<hr style='margin:0.1rem 0px;'/>", unsafe_allow_html=True)

# ==========================================
# 2. PLANTILLA MANUAL DE GASTOS
# ==========================================
elif opcion == "📝 Plantilla de Gastos (Sin Ticket)":
    st.title("📝 Plantilla de Gastos Directos")
    st.info("💡 Usa esta pantalla para registrar cafés, cañas, Bizums o compras sin ticket físico.")
    
    tab1, tab2 = st.tabs(["⚡ Gasto Rápido Individual", "📋 Plantilla Múltiple"])
    
    with tab1:
        with st.form("form_gasto_individual", clear_on_submit=True):
            c1, c2 = st.columns(2)
            with c1:
                cat_ind = st.selectbox("Categoría", CATEGORIAS)
                conc_ind = st.text_input("Concepto (ej. Desayuno bar, Bizum cena, Abono plantas)")
                fecha_ind = st.date_input("Fecha", datetime.now().date())
            with c2:
                monto_ind = st.number_input("Importe (€)", min_value=0.10, step=1.0)
                metodo_ind = st.selectbox("Método de Pago", ["Tarjeta/PayPal", "Efectivo"])
                
            if st.form_submit_button("Guardar Gasto") and conc_ind and monto_ind > 0:
                con_w = init_connection()
                cur_w = con_w.cursor()
                cur_w.execute("INSERT INTO movimientos_caja (fecha, tipo, categoria, concepto, monto, metodo_pago) VALUES (%s, 'Gasto', %s, %s, %s, %s)",
                              (fecha_ind, cat_ind, conc_ind, monto_ind, metodo_ind))
                con_w.commit()
                con_w.close()
                st.success(f"Gasto de {monto_ind:.2f} € en '{cat_ind}' registrado con éxito.")
                st.rerun()

    with tab2:
        st.caption("Rellena las filas para volcar varios gastos de una sola vez.")
        
        if "filas_plantilla" not in st.session_state:
            st.session_state["filas_plantilla"] = pd.DataFrame([
                {"Fecha": datetime.now().date(), "Categoría": CATEGORIAS[0], "Concepto": "", "Importe": 0.0, "Método": "Tarjeta/PayPal"}
            ])
            
        df_editor = st.data_editor(
            st.session_state["filas_plantilla"],
            num_rows="dynamic",
            column_config={
                "Fecha": st.column_config.DateColumn("Fecha", required=True),
                "Categoría": st.column_config.SelectboxColumn("Categoría", options=CATEGORIAS, required=True),
                "Concepto": st.column_config.TextColumn("Concepto", required=True),
                "Importe": st.column_config.NumberColumn("Importe (€)", min_value=0.01, format="%.2f €", required=True),
                "Método": st.column_config.SelectboxColumn("Método", options=["Tarjeta/PayPal", "Efectivo"], required=True)
            },
            width="stretch",
            key="editor_gastos"
        )
        
        if st.button("🚀 Inyectar Todos los Gastos"):
            gastos_validos = df_editor[(df_editor["Importe"] > 0) & (df_editor["Concepto"].str.strip() != "")]
            if not gastos_validos.empty:
                con_w = init_connection()
                cur_w = con_w.cursor()
                for _, g in gastos_validos.iterrows():
                    cur_w.execute("INSERT INTO movimientos_caja (fecha, tipo, categoria, concepto, monto, metodo_pago) VALUES (%s, 'Gasto', %s, %s, %s, %s)",
                                  (g["Fecha"], g["Categoría"], g["Concepto"].strip(), g["Importe"], g["Método"]))
                con_w.commit()
                con_w.close()
                st.session_state["filas_plantilla"] = pd.DataFrame([
                    {"Fecha": datetime.now().date(), "Categoría": CATEGORIAS[0], "Concepto": "", "Importe": 0.0, "Método": "Tarjeta/PayPal"}
                ])
                st.success(f"Se han registrado {len(gastos_validos)} gastos correctamente.")
                st.rerun()
            else:
                st.warning("No hay gastos con importe y concepto válidos para guardar.")

# ==========================================
# 3. LECTOR DE TICKETS IA
# ==========================================
elif opcion == "📷 Lector de Tickets IA":
    st.title("📷 Escáner Contable de Tickets")
    api_key = st.secrets.get("GEMINI_API_KEY", "")
    
    if not api_key:
        st.warning("Configura tu GEMINI_API_KEY en los Secrets de Streamlit.")
    else:
        archivo = st.file_uploader("Sube la foto del ticket:", type=["jpg", "jpeg", "png"])
        if archivo:
            st.image(Image.open(archivo), width=240)
            if st.button("🔍 Analizar y Categorizar Ticket"):
                with st.spinner("La IA está leyendo y sumando categorías..."):
                    try:
                        client = genai.Client(api_key=api_key)
                        
                        prompt = f"""Analiza este ticket y devuelve EXCLUSIVAMENTE un JSON válido.
                        Tu objetivo es agrupar y sumar todos los importes en las categorías que apliquen:
                        {CATEGORIAS}
                        
                        Formato exacto:
                        {{
                          "comercio": "Nombre del establecimiento",
                          "metodo_pago": "Tarjeta/PayPal o Efectivo",
                          "desglose": [
                            {{"categoria": "Nombre de la categoría exacta", "monto": 0.00}}
                          ]
                        }}
                        Solo incluye categorías con monto > 0. No detalles productos individuales."""
                        
                        uploaded = client.files.upload(file=archivo)
                        res = client.models.generate_content(model='gemini-2.5-flash', contents=[uploaded, prompt])
                        txt = res.text.strip()
                        
                        if "```json" in txt:
                            txt = txt.split("```json")[1].split("```")[0]
                        elif "```" in txt:
                            txt = txt.split("```")[1].split("```")[0]
                            
                        st.session_state["ticket_procesado"] = json.loads(txt.strip())
                        st.success("Ticket analizado.")
                    except Exception as e:
                        st.error(f"Error procesando el ticket: {e}")
                        
        if "ticket_procesado" in st.session_state:
            data = st.session_state["ticket_procesado"]
            st.markdown("---")
            st.subheader(f"🏪 Establecimiento: {data.get('comercio', 'Desconocido')}")
            
            c_p1, c_p2 = st.columns(2)
            with c_p1:
                metodo_sel = st.selectbox("Método de Pago:", ["Tarjeta/PayPal", "Efectivo"], 
                                         index=0 if "tarjeta" in data.get('metodo_pago', '').lower() else 1)
            with c_p2:
                fecha_sel = st.date_input("Fecha del Ticket:", datetime.now().date())
                
            df_ticket = pd.DataFrame(data.get("desglose", []))
            if not df_ticket.empty:
                df_ticket_edit = st.data_editor(df_ticket, width="stretch", key="edit_ticket_preview")
                total_t = df_ticket_edit["monto"].sum()
                st.write(f"**Total a registrar:** `{total_t:,.2f} €`")
                
                if st.button("📥 Confirmar e Inyectar a Contabilidad"):
                    con_w = init_connection()
                    cur_w = con_w.cursor()
                    comercio_nom = data.get('comercio', 'Ticket')
                    
                    for _, row in df_ticket_edit.iterrows():
                        cur_w.execute("INSERT INTO movimientos_caja (fecha, tipo, categoria, concepto, monto, metodo_pago) VALUES (%s, 'Gasto', %s, %s, %s, %s)",
                                      (fecha_sel, row['categoria'], f"Compra {comercio_nom}", row['monto'], metodo_sel))
                    con_w.commit()
                    con_w.close()
                    del st.session_state["ticket_procesado"]
                    st.success("Gastos cargados en la caja.")
                    st.rerun()

# ==========================================
# 4. PREVISIONES Y AHORRO
# ==========================================
elif opcion == "🔮 Previsiones y Ahorro":
    st.title("🔮 Motor de Previsión y Ahorro Libre")
    
    con = init_connection()
    df_gastos = pd.read_sql_query("SELECT fecha, categoria, monto FROM movimientos_caja WHERE tipo='Gasto'", con)
    cur = con.cursor()
    cur.execute("SELECT nomina_estimada, fondo_emergencia FROM config_sistema WHERE id=1")
    nomina, airbag_actual = cur.fetchone()
    cur.execute("SELECT COALESCE(SUM(monto), 0) FROM gastos_recurrentes")
    total_fijos = cur.fetchone()[0]
    cur.execute("SELECT COALESCE(SUM(monto_total / meses_totales), 0) FROM compras_plazos WHERE meses_pagados < meses_totales")
    total_plazos = cur.fetchone()[0]
    cur.execute("SELECT COALESCE(SUM(monto_total / 12.0), 0) FROM previsiones_anuales")
    total_anuales = cur.fetchone()[0]
    con.close()
    
    # Calcular medias históricas por mes
    medias_cat = {}
    if not df_gastos.empty:
        df_gastos['mes'] = pd.to_datetime(df_gastos['fecha']).dt.strftime('%Y-%m')
        meses_totales = max(1, df_gastos['mes'].nunique())
        
        for cat in CATEGORIAS:
            suma_cat = df_gastos[df_gastos['categoria'] == cat]['monto'].sum()
            medias_cat[cat] = float(suma_cat) / meses_totales
    else:
        medias_cat = {c: 0.0 for c in CATEGORIAS}
        
    gasto_medio_variables = sum(medias_cat.values())
    coste_vida_mensual = float(total_fijos) + float(total_plazos) + float(total_anuales) + gasto_medio_variables
    ahorro_libre = float(nomina) - coste_vida_mensual
    
    st.markdown("### 📊 Radiografía de tu Mes Típico")
    c1, c2, c3 = st.columns(3)
    c1.metric("💵 Nómina Estimada", f"{float(nomina):,.2f} €")
    c2.metric("📉 Coste de Vida Medio", f"{coste_vida_mensual:,.2f} €")
    c3.metric("💰 Ahorro Libre Disponible", f"{ahorro_libre:,.2f} €", delta=f"{ahorro_libre:,.2f} €")
    
    st.markdown("---")
    st.subheader("📋 Consumo Medio Histórico por Categoría")
    
    cols_m = st.columns(4)
    for idx, (cat, val) in enumerate(medias_cat.items()):
        cols_m[idx % 4].metric(cat, f"{val:,.2f} €/mes")
        
    st.markdown("---")
    st.subheader("🛡️ Blindaje: Fondo de Emergencia (Airbag)")
    meta_airbag = coste_vida_mensual * 2.5
    progreso_airbag = min(100.0, (float(airbag_actual) / meta_airbag) * 100) if meta_airbag > 0 else 100.0
    
    st.progress(progreso_airbag / 100.0)
    st.caption(f"Tienes **{float(airbag_actual):,.2f} €** de una meta recomendada de **{meta_airbag:,.2f} €** (2.5 meses de gastos).")
    
    with st.form("form_airbag"):
        ca1, ca2 = st.columns([8, 2])
        with ca1: aporte_airbag = st.number_input("Aportar al Fondo de Emergencia desde el Banco (€)", min_value=0.0, step=50.0)
        with ca2: 
            st.write("")
            btn_airbag = st.form_submit_button("Abonar al Fondo")
            
        if btn_airbag and aporte_airbag > 0:
            con_w = init_connection()
            cur_w = con_w.cursor()
            cur_w.execute("UPDATE config_sistema SET fondo_emergencia = fondo_emergencia + %s WHERE id=1", (aporte_airbag,))
            cur_w.execute("INSERT INTO movimientos_caja (fecha, tipo, categoria, concepto, monto, metodo_pago) VALUES (%s, 'Gasto', 'Ahorro', 'Abono Fondo Emergencia', %s, 'Tarjeta/PayPal')",
                          (datetime.now().date(), aporte_airbag))
            con_w.commit()
            con_w.close()
            st.success("Dinero blindado en el fondo.")
            st.rerun()

# ==========================================
# 5. GASTOS FIJOS Y PLAZOS
# ==========================================
elif opcion == "🔄 Gastos Fijos y Plazos":
    st.title("🔄 Gestión de Gastos Fijos y Financiaciones")
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("📅 Gastos Fijos Mensuales (Luz, Internet, Gym)")
        with st.form("form_fijo", clear_on_submit=True):
            nom_f = st.text_input("Concepto")
            mon_f = st.number_input("Importe (€/mes)", min_value=1.0, step=5.0)
            if st.form_submit_button("Guardar Fijo") and nom_f:
                try:
                    con_w = init_connection()
                    cur_w = con_w.cursor()
                    cur_w.execute("INSERT INTO gastos_recurrentes (nombre_gasto, monto) VALUES (%s, %s)", (nom_f, mon_f))
                    con_w.commit()
                    con_w.close()
                    st.rerun()
                except IntegrityError:
                    st.error("Ya existe ese gasto fijo.")
                    
        con = init_connection()
        df_f = pd.read_sql_query("SELECT * FROM gastos_recurrentes", con)
        con.close()
        for _, r in df_f.iterrows():
            cf1, cf2 = st.columns([8, 2])
            cf1.write(f"▫️ **{r['nombre_gasto']}**: {float(r['monto']):,.2f} €/mes")
            if cf2.button("🗑️", key=f"delf_{r['id']}"):
                con_w = init_connection()
                cur_w = con_w.cursor()
                cur_w.execute("DELETE FROM gastos_recurrentes WHERE id=%s", (r['id'],))
                con_w.commit()
                con_w.close()
                st.rerun()
                
    with col2:
        st.subheader("💳 Compras a Plazos")
        with st.form("form_plazo", clear_on_submit=True):
            art_p = st.text_input("Artículo")
            tot_p = st.number_input("Total (€)", min_value=10.0, step=50.0)
            mes_p = st.number_input("Meses", min_value=1, step=1, value=12)
            if st.form_submit_button("Guardar Financiación") and art_p:
                try:
                    con_w = init_connection()
                    cur_w = con_w.cursor()
                    cur_w.execute("INSERT INTO compras_plazos (articulo, monto_total, meses_totales, meses_pagados) VALUES (%s, %s, %s, 0)", (art_p, tot_p, mes_p))
                    con_w.commit()
                    con_w.close()
                    st.rerun()
                except IntegrityError:
                    st.error("Ya existe.")
                    
        con = init_connection()
        df_p = pd.read_sql_query("SELECT * FROM compras_plazos", con)
        con.close()
        for _, r in df_p.iterrows():
            cuota = float(r['monto_total']) / r['meses_totales']
            st.write(f"💳 **{r['articulo']}** — Cuota: **{cuota:,.2f} €/mes** ({r['meses_pagados']}/{r['meses_totales']})")
            st.progress(r['meses_pagados'] / r['meses_totales'])
            if st.button("🗑️ Eliminar Financiación", key=f"delp_{r['id']}"):
                con_w = init_connection()
                cur_w = con_w.cursor()
                cur_w.execute("DELETE FROM compras_plazos WHERE id=%s", (r['id'],))
                con_w.commit()
                con_w.close()
                st.rerun()

# ==========================================
# 6. PROVISIONES ANUALES
# ==========================================
elif opcion == "🗓️ Provisiones Anuales":
    st.title("🗓️ Huchas para Pagos Anuales (Sinking Funds)")
    st.caption("Divide los gastos de una sola vez al año (Seguro, IBI) entre 12 meses.")
    
    col1, col2 = st.columns([4, 6])
    with col1:
        with st.form("form_prev", clear_on_submit=True):
            conc_prev = st.text_input("Concepto (ej. Seguro Coche, IBI)")
            mon_prev = st.number_input("Coste Anual (€)", min_value=10.0, step=50.0)
            mes_prev = st.selectbox("Mes de Cobro", list(range(1, 13)), format_func=lambda x: ["Enero","Febrero","Marzo","Abril","Mayo","Junio","Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"][x-1])
            if st.form_submit_button("Crear Previsión") and conc_prev:
                try:
                    con_w = init_connection()
                    cur_w = con_w.cursor()
                    cur_w.execute("INSERT INTO previsiones_anuales (concepto, monto_total, mes_objetivo) VALUES (%s, %s, %s)", (conc_prev, mon_prev, mes_prev))
                    con_w.commit()
                    con_w.close()
                    st.rerun()
                except IntegrityError:
                    st.error("Ya existe una previsión con ese nombre.")
                    
    with col2:
        con = init_connection()
        df_pr = pd.read_sql_query("SELECT * FROM previsiones_anuales ORDER BY mes_objetivo ASC", con)
        con.close()
        meses = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]
        for _, r in df_pr.iterrows():
            c_info, c_pag, c_borr = st.columns([6, 3, 1])
            c_info.write(f"🎯 **{r['concepto']}**: {float(r['monto_total']):,.2f} € (en {meses[r['mes_objetivo']-1]})")
            c_info.caption(f"🛡️ Guardar: **{float(r['monto_total'])/12.0:,.2f} €/mes**")
            if c_pag.button("💳 Pagar Ahora", key=f"pag_pr_{r['id']}"):
                con_w = init_connection()
                cur_w = con_w.cursor()
                cur_w.execute("INSERT INTO movimientos_caja (fecha, tipo, categoria, concepto, monto, metodo_pago) VALUES (%s, 'Gasto', 'Gasto Anual', %s, %s, 'Tarjeta/PayPal')",
                              (datetime.now().date(), f"Pago: {r['concepto']}", r['monto_total']))
                con_w.commit()
                con_w.close()
                st.success("Pago anual cargado en la caja.")
                st.rerun()
            if c_borr.button("❌", key=f"del_pr_{r['id']}"):
                con_w = init_connection()
                cur_w = con_w.cursor()
                cur_w.execute("DELETE FROM previsiones_anuales WHERE id=%s", (r['id'],))
                con_w.commit()
                con_w.close()
                st.rerun()

# ==========================================
# 7. ANÁLISIS Y ESTADÍSTICAS
# ==========================================
elif opcion == "📊 Análisis y Estadísticas":
    st.title("📊 Análisis de Gastos por Categoría")
    con = init_connection()
    df = pd.read_sql_query("SELECT fecha, categoria, monto FROM movimientos_caja WHERE tipo='Gasto'", con)
    con.close()
    
    if df.empty:
        st.info("Aún no tienes gastos registrados para analizar.")
    else:
        df['monto'] = df['monto'].astype(float)
        df['mes'] = pd.to_datetime(df['fecha']).dt.strftime('%Y-%m')
        
        st.subheader("📉 Gasto Mensual Agrupado")
        pivot = df.pivot_table(index='mes', columns='categoria', values='monto', aggfunc='sum', fill_value=0)
        st.bar_chart(pivot)
        
        st.markdown("---")
        st.subheader("🏆 Distribución Total Histórica")
        c1, c2 = st.columns(2)
        total_acum = df['monto'].sum()
        c1.metric("Gasto Total Histórico", f"{total_acum:,.2f} €")
        resumen_cat = df.groupby('categoria')['monto'].sum().sort_values(ascending=False)
        c2.dataframe(resumen_cat.map("{:,.2f} €".format), width='stretch')

# ==========================================
# 8. CONFIGURACIÓN Y RESET
# ==========================================
elif opcion == "⚙️ Configuración":
    st.title("⚙️ Configuración y Puesta a Punto")
    con = init_connection()
    cur = con.cursor()
    cur.execute("SELECT nomina_estimada FROM config_sistema WHERE id=1")
    nom_actual = cur.fetchone()[0]
    con.close()
    
    with st.form("form_config"):
        nueva_nom = st.number_input("Tu Nómina Fija Mensual (€)", value=float(nom_actual), step=50.0)
        if st.form_submit_button("Actualizar Nómina"):
            con_w = init_connection()
            cur_w = con_w.cursor()
            cur_w.execute("UPDATE config_sistema SET nomina_estimada=%s WHERE id=1", (nueva_nom,))
            con_w.commit()
            con_w.close()
            st.success("Nómina actualizada.")
            st.rerun()
            
    st.markdown("---")
    st.subheader("🚨 Reset de Base de Datos")
    st.caption("Borra todas las tablas y las crea limpias con la estructura nueva.")
    if st.button("💣 Resetear Todo a Fábrica"):
        con_w = init_connection()
        con_w.autocommit = True
        cur_w = con_w.cursor()
        tablas = ["movimientos_caja", "gastos_recurrentes", "compras_plazos", "previsiones_anuales", "proyectos_futuros", "config_sistema"]
        for t in tablas:
            cur_w.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
        con_w.close()
        arrancar_sistema.clear()
        st.success("Sistema reiniciado.")
        st.rerun()
