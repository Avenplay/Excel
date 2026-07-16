import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from PIL import Image
import json
import os
from google import genai
import psycopg2
from psycopg2 import IntegrityError, OperationalError
import warnings

# Silenciamos el aviso de Pandas sobre SQLAlchemy para mantener la consola limpia en producción
warnings.filterwarnings('ignore', 'pandas only supports SQLAlchemy connectable')

# --- BLOQUE DE SEGURIDAD ---
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
    else:
        return True

if not check_password():
    st.stop()

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Copiloto Financiero Doméstico", layout="wide")
LISTA_SUPERS = ["Mercadona", "Lidl", "Carrefour", "Aldi", "Family Cash", "Dia", "Otros"]

# --- FUNCIÓN CENTRALIZADA DE CONEXIÓN A POSTGRESQL ---
def init_connection():
    return psycopg2.connect(st.secrets["DATABASE_URL"])

# --- INICIALIZACIÓN DE LA BASE DE DATOS ---
def inicializar_base_datos():
    conexion = init_connection()
    conexion.autocommit = True
    cursor = conexion.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS movimientos_caja (
        id SERIAL PRIMARY KEY, fecha TEXT NOT NULL, concepto TEXT NOT NULL,
        monto REAL NOT NULL, tipo_ingreso_gasto TEXT, metodo_pago TEXT, subcuenta_extra TEXT DEFAULT 'N/A'
    )""")
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS despensa (
        id SERIAL PRIMARY KEY, producto_generico TEXT NOT NULL, supermercado TEXT NOT NULL,
        unidades_actuales INTEGER NOT NULL DEFAULT 0, peso_neto_kg REAL NOT NULL, precio_unitario REAL NOT NULL,
        fecha_compra TEXT, ubicacion TEXT DEFAULT 'Armario'
    )""")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS consumo_alimentos (
        id SERIAL PRIMARY KEY, fecha TEXT NOT NULL, producto_generico TEXT NOT NULL,
        cantidad INTEGER NOT NULL, coste_estimado REAL NOT NULL, estado TEXT
    )""")
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS proyectos_futuros (
        id SERIAL PRIMARY KEY, nombre_proyecto TEXT NOT NULL UNIQUE,
        objetivo_total REAL NOT NULL, meses_restantes INTEGER NOT NULL, ahorrado_acumulado REAL NOT NULL DEFAULT 0.0
    )""")
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS gastos_recurrentes (
        id SERIAL PRIMARY KEY, nombre_gasto TEXT NOT NULL UNIQUE, monto REAL NOT NULL
    )""")
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS compras_plazos (
        id SERIAL PRIMARY KEY, articulo TEXT NOT NULL UNIQUE,
        monto_total REAL NOT NULL, meses_totales INTEGER NOT NULL, meses_pagados INTEGER NOT NULL DEFAULT 0
    )""")
        
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS utensilios (
        id SERIAL PRIMARY KEY, producto_generico TEXT NOT NULL, supermercado TEXT NOT NULL,
        unidades_actuales INTEGER NOT NULL DEFAULT 0, peso_neto_kg REAL NOT NULL, precio_unitario REAL NOT NULL,
        fecha_compra TEXT
    )""")
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS lista_compra (
        id SERIAL PRIMARY KEY, producto TEXT NOT NULL, supermercado_recomendado TEXT NOT NULL
    )""")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS configuracion_coche (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        letra_mensual REAL NOT NULL DEFAULT 0.0,
        seguro_mensual REAL NOT NULL DEFAULT 0.0
    )""")
    cursor.execute("INSERT INTO configuracion_coche (id, letra_mensual, seguro_mensual) VALUES (1, 0.0, 0.0) ON CONFLICT (id) DO NOTHING")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS previsiones_anuales (
        id SERIAL PRIMARY KEY, concepto TEXT NOT NULL UNIQUE,
        monto_total REAL NOT NULL, mes_objetivo INTEGER NOT NULL
    )""")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS fondo_emergencia (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        acumulado REAL NOT NULL DEFAULT 0.0
    )""")
    cursor.execute("INSERT INTO fondo_emergencia (id, acumulado) VALUES (1, 0.0) ON CONFLICT (id) DO NOTHING")

    conexion.close()

def ejecutar_automatizaciones_mensuales():
    mes_actual = datetime.now().strftime("%Y-%m")
    conexion = init_connection()
    cursor = conexion.cursor()
    
    cursor.execute("SELECT nombre_gasto, monto FROM gastos_recurrentes")
    for nombre, monto in cursor.fetchall():
        concepto_cargo = f"Fijo Automático: {nombre}"
        cursor.execute("SELECT COUNT(*) FROM movimientos_caja WHERE concepto = %s AND fecha LIKE %s", (concepto_cargo, f"{mes_actual}%"))
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO movimientos_caja (fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago) VALUES (%s, %s, %s, 'Gasto Habitual', 'Tarjeta/PayPal')", 
                           (datetime.now().strftime("%Y-%m-%d"), concepto_cargo, monto))
            
    cursor.execute("SELECT id, articulo, monto_total, meses_totales, meses_pagados FROM compras_plazos WHERE meses_pagados < meses_totales")
    for pid, articulo, total, m_totales, m_pagados in cursor.fetchall():
        concepto_base = f"Cuota Plazo: {articulo} (%%"
        cursor.execute("SELECT COUNT(*) FROM movimientos_caja WHERE concepto LIKE %s AND fecha LIKE %s", (concepto_base, f"{mes_actual}%"))
        if cursor.fetchone()[0] == 0:
            concepto_cuota = f"Cuota Plazo: {articulo} ({m_pagados + 1}/{m_totales})"
            cursor.execute("INSERT INTO movimientos_caja (fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago) VALUES (%s, %s, %s, 'Gasto Habitual', 'Tarjeta/PayPal')", 
                           (datetime.now().strftime("%Y-%m-%d"), concepto_cuota, total / m_totales))
            cursor.execute("UPDATE compras_plazos SET meses_pagados = meses_pagados + 1 WHERE id = %s", (pid,))
            
    conexion.commit()
    conexion.close()

# Caché agresivo para que las automatizaciones solo se miren 1 vez y no ralenticen tu navegación
@st.cache_resource
def arrancar_sistema():
    inicializar_base_datos()
    ejecutar_automatizaciones_mensuales()
    return True

arrancar_sistema()

# --- FUNCIONES AUXILIARES DE ESCRITURA ---
def registrar_movimiento(concepto, monto, tipo, metodo, subcuenta='N/A'):
    conexion = init_connection()
    cursor = conexion.cursor()
    cursor.execute("INSERT INTO movimientos_caja (fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago, subcuenta_extra) VALUES (%s, %s, %s, %s, %s, %s)", 
                   (datetime.now().strftime("%Y-%m-%d"), concepto, monto, tipo, metodo, subcuenta))
    conexion.commit()
    conexion.close()

def obtener_mejor_super(producto_nombre, tabla="despensa"):
    conexion = init_connection()
    cursor = conexion.cursor()
    fecha_limite = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
    q = f"SELECT supermercado FROM {tabla} WHERE producto_generico = %s AND fecha_compra >= %s GROUP BY supermercado ORDER BY AVG(precio_unitario/peso_neto_kg) ASC LIMIT 1"
    cursor.execute(q, (producto_nombre.lower().strip(), fecha_limite))
    res = cursor.fetchone()
    conexion.close()
    return res[0] if res else "Cualquiera"

def añadir_a_lista_compra(producto, super_rec):
    conexion = init_connection()
    cursor = conexion.cursor()
    cursor.execute("SELECT COUNT(*) FROM lista_compra WHERE producto = %s", (producto,))
    ext = cursor.fetchone()[0]
    if ext == 0:
        cursor.execute("INSERT INTO lista_compra (producto, supermercado_recomendado) VALUES (%s, %s)", (producto, super_rec))
        conexion.commit()
    conexion.close()

# --- SIDEBAR NAVEGACIÓN ---
st.sidebar.title("📌 Menú Principal")
opcion_menu = st.sidebar.radio("Ir a:", [
    "💵 Control de Caja", 
    "🍏 Despensa (Alimentos)", 
    "🏠 Utensilios (Hogar)",
    "🛒 Lista de la Compra",
    "🔄 Gastos Recurrentes", 
    "💳 Compras a Plazos", 
    "🗓️ Previsiones Anuales", 
    "🔮 Previsiones y Proyectos", 
    "🚗 Mi Coche",
    "📊 Análisis y Resumen Anual",
    "📷 Lector de Tickets IA",
    "⚙️ Configuración y Arranque"
])

# ==========================================
# VISTAS DE LA APLICACIÓN (CARGA BAJO DEMANDA)
# ==========================================

if opcion_menu == "💵 Control de Caja":
    st.title("🧠 Tu Copiloto Financiero Inteligente")
    st.markdown("---")
    
    conexion = init_connection()
    cursor = conexion.cursor()
    cursor.execute("""
        SELECT 
            COALESCE(SUM(CASE WHEN tipo_ingreso_gasto='Ingreso Fijo' THEN monto ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN subcuenta_extra='Extra-Banco' THEN monto ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN tipo_ingreso_gasto LIKE 'Gasto%%' AND metodo_pago='Tarjeta/PayPal' THEN monto ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN subcuenta_extra='Extra-Efectivo' THEN monto ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN tipo_ingreso_gasto LIKE 'Gasto%%' AND metodo_pago='Efectivo' THEN monto ELSE 0 END), 0)
        FROM movimientos_caja
    """)
    ingresos_fijos, extras_banco, gastos_tarjeta, extras_efectivo, gastos_efectivo = cursor.fetchone()
    
    saldo_banco = ingresos_fijos + extras_banco - gastos_tarjeta
    saldo_efectivo = extras_efectivo - gastos_efectivo
    
    cursor.execute("SELECT COALESCE(SUM(monto_total / 12.0), 0) FROM previsiones_anuales")
    total_provisiones_mes = cursor.fetchone()[0]
    cursor.execute("SELECT COALESCE(SUM(unidades_actuales * precio_unitario), 0) FROM despensa")
    inmovilizado_comida = cursor.fetchone()[0]
    cursor.execute("SELECT COALESCE(SUM(unidades_actuales * precio_unitario), 0) FROM utensilios")
    inmovilizado_hogar = cursor.fetchone()[0]
    
    df_movimientos = pd.read_sql_query("SELECT * FROM movimientos_caja ORDER BY id DESC", conexion)
    conexion.close()
    
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1: st.metric(label="💳 Bolsa Única", value=f"{saldo_banco:,.2f} €")
    with col2: st.metric(label="💵 Hucha Efectivo", value=f"{saldo_efectivo:,.2f} €")
    with col3: st.metric(label="🛡️ Colchón Provisiones", value=f"{total_provisiones_mes:,.2f} €", help="Dinero de tu Bolsa Única que NO debes tocar este mes para poder pagar seguros/IBI a futuro.")
    with col4: st.metric(label="📦 Stock Comida", value=f"{inmovilizado_comida:,.2f} €")
    with col5: st.metric(label="🧴 Stock Hogar", value=f"{inmovilizado_hogar:,.2f} €")
    
    st.markdown("---")
    col_l, col_r = st.columns(2)
    
    with col_l:
        st.header("🛒 Registrar Gasto Manual")
        categoria_gasto = st.selectbox("Categoría del Gasto", ["Alimentación", "Hogar", "Gastos Fijos", "Gasto Excepcional"])
        
        if categoria_gasto in ["Alimentación", "Hogar"]:
            with st.form("form_gasto_inventario", clear_on_submit=True):
                st.caption("Al guardar, el importe se restará del banco y los artículos irán a su despensa correspondiente.")
                concepto_g = st.text_input("Producto / Concepto")
                c_a, c_b = st.columns(2)
                with c_a:
                    monto_g = st.number_input("Importe Total (€)", min_value=0.0, step=0.50)
                    unidades_g = st.number_input("Unidades Compradas", min_value=1, step=1)
                with c_b:
                    super_g = st.selectbox("Supermercado", LISTA_SUPERS)
                    peso_g = st.number_input("Peso/L por ud.", min_value=0.01, value=1.0) if categoria_gasto == "Alimentación" else 1.0
                
                metodo_g = st.selectbox("Pago", ["Tarjeta/PayPal", "Efectivo"])
                
                if st.form_submit_button("Guardar Gasto y Añadir a Stock") and concepto_g and monto_g > 0:
                    precio_uni = monto_g / unidades_g
                    fecha_actual = datetime.now().strftime("%Y-%m-%d")
                    tabla_destino = "despensa" if categoria_gasto == "Alimentación" else "utensilios"
                    
                    conexion_w = init_connection()
                    cursor_w = conexion_w.cursor()
                    
                    cursor_w.execute("INSERT INTO movimientos_caja (fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago) VALUES (%s, %s, %s, %s, %s)", 
                                   (fecha_actual, f"{categoria_gasto}: {concepto_g}", monto_g, "Gasto Habitual", metodo_g))
                    
                    if tabla_destino == "despensa":
                        cursor_w.execute("INSERT INTO despensa (producto_generico, supermercado, unidades_actuales, peso_neto_kg, precio_unitario, fecha_compra, ubicacion) VALUES (%s, %s, %s, %s, %s, %s, 'Armario')", 
                                       (concepto_g.lower().strip(), super_g, unidades_g, peso_g, precio_uni, fecha_actual))
                    else:
                        cursor_w.execute("INSERT INTO utensilios (producto_generico, supermercado, unidades_actuales, peso_neto_kg, precio_unitario, fecha_compra) VALUES (%s, %s, %s, %s, %s, %s)", 
                                       (concepto_g.lower().strip(), super_g, unidades_g, peso_g, precio_uni, fecha_actual))
                        
                    conexion_w.commit()
                    conexion_w.close()
                    st.rerun()

        elif categoria_gasto == "Gastos Fijos":
            with st.form("form_gasto_fijo", clear_on_submit=True):
                st.caption("Al guardar, se cobrará hoy y se añadirá a la pestaña 'Gastos Recurrentes' para el futuro.")
                concepto_g = st.text_input("Concepto del Gasto Fijo")
                monto_g = st.number_input("Importe (€)", min_value=0.0, step=0.50)
                metodo_g = st.selectbox("Pago", ["Tarjeta/PayPal", "Efectivo"])
                
                if st.form_submit_button("Guardar Gasto Fijo") and concepto_g and monto_g > 0:
                    conexion_w = init_connection()
                    cursor_w = conexion_w.cursor()
                    try:
                        cursor_w.execute("INSERT INTO gastos_recurrentes (nombre_gasto, monto) VALUES (%s, %s)", (concepto_g, monto_g))
                        cursor_w.execute("INSERT INTO movimientos_caja (fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago) VALUES (%s, %s, %s, %s, %s)", 
                                       (datetime.now().strftime("%Y-%m-%d"), f"Fijo Automático: {concepto_g}", monto_g, "Gasto Habitual", metodo_g))
                        conexion_w.commit()
                        st.rerun()
                    except IntegrityError:
                        st.error("⚠️ Ya existe un gasto fijo recurrente con ese nombre exacto.")
                    finally:
                        conexion_w.close()

        else:
            with st.form("form_gasto_excepcional", clear_on_submit=True):
                concepto_g = st.text_input("Concepto (Ej. Taller, Cena)")
                monto_g = st.number_input("Importe (€)", min_value=0.0, step=0.50)
                metodo_g = st.selectbox("Pago", ["Tarjeta/PayPal", "Efectivo"])
                
                if st.form_submit_button("Guardar Gasto Excepcional") and concepto_g and monto_g > 0:
                    registrar_movimiento(concepto_g, monto_g, "Gasto Excepcional", metodo_g)
                    st.rerun()

    with col_r:
        st.header("💰 Registrar Entrada de Dinero")
        with st.form("form_ingreso", clear_on_submit=True):
            concepto_i = st.text_input("Concepto")
            monto_i = st.number_input("Importe (€)", min_value=0.0, step=50.0)
            tipo_i = st.selectbox("Categoría", ["Ingreso Fijo", "Ingreso Extra"])
            subcuenta_i = st.selectbox("Destino Extra", ["N/A", "Extra-Efectivo", "Extra-Banco"]) if tipo_i == "Ingreso Extra" else "N/A"
            metodo_i = "Efectivo" if subcuenta_i == "Extra-Efectivo" else "Tarjeta/PayPal"
            if st.form_submit_button("Guardar Entrada") and concepto_i and monto_i > 0:
                registrar_movimiento(concepto_i, monto_i, tipo_i, metodo_i, subcuenta_i)
                st.rerun()
                
    st.markdown("---")
    st.header("📜 Historial de Movimientos")
    
    if not df_movimientos.empty:
        df_chrono = df_movimientos.iloc[::-1].copy()
        running_banco = 0.0
        running_hucha = 0.0
        saldos_registro = {}
        
        for idx, row in df_chrono.iterrows():
            m_id, monto, tipo, pago, sub = row['id'], row['monto'], row['tipo_ingreso_gasto'], row['metodo_pago'], row['subcuenta_extra']
            if tipo == 'Ingreso Fijo': 
                running_banco += monto
            elif tipo == 'Ingreso Extra':
                if sub == 'Extra-Banco': running_banco += monto
                elif sub == 'Extra-Efectivo': running_hucha += monto
            elif tipo.startswith('Gasto') or tipo in ['Gasto Habitual', 'Gasto Excepcional'] or tipo in ['Alimentación', 'Hogar', 'Gasto Coche']:
                if pago == 'Tarjeta/PayPal': running_banco -= monto
                elif pago == 'Efectivo': running_hucha -= monto
            saldos_registro[m_id] = (running_banco, running_hucha)
            
        for index, fila in df_movimientos.iterrows():
            m_id, m_fecha, m_concepto, m_monto, m_tipo, m_pago, m_sub = fila['id'], fila['fecha'], fila['concepto'], fila['monto'], fila['tipo_ingreso_gasto'], fila['metodo_pago'], fila['subcuenta_extra']
            
            if m_concepto.startswith("Saldo Inicial:"):
                continue
                
            bal_banco, bal_hucha = saldos_registro.get(m_id, (0.0, 0.0))
            txt_balance = f"💵 Balance Hucha: **{bal_hucha:,.2f} €**" if (m_pago == 'Efectivo' or m_sub == 'Extra-Efectivo') else f"🏦 Balance Banco: **{bal_banco:,.2f} €**"
            
            c_detalles, c_eliminar = st.columns([8, 2])
            with c_detalles: 
                st.write(f"📅 **{m_fecha}** | `{m_tipo}` | **{m_concepto}** -> **{m_monto:,.2f} €** ({m_pago}) | {txt_balance}")
            with c_eliminar:
                if st.button("🗑️ Borrar", key=f"del_mov_{m_id}"):
                    conexion_d = init_connection()
                    cursor_d = conexion_d.cursor()
                    cursor_d.execute("DELETE FROM movimientos_caja WHERE id = %s", (m_id,))
                    conexion_d.commit()
                    conexion_d.close()
                    st.rerun()
            st.markdown("<hr style='margin:0.2rem 0px;'/>", unsafe_allow_html=True)

elif opcion_menu == "🍏 Despensa (Alimentos)":
    st.title("🛒 Despensa de Alimentos")
    
    conexion = init_connection()
    cursor = conexion.cursor()
    cursor.execute("""
        SELECT 
            COALESCE(SUM(CASE WHEN estado='Consumido' THEN coste_estimado ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN estado='Tirado' THEN coste_estimado ELSE 0 END), 0)
        FROM consumo_alimentos
    """)
    coste_comida, mermas_comida = cursor.fetchone()
    
    df_stock = pd.read_sql_query("SELECT * FROM despensa WHERE unidades_actuales > 0", conexion)
    conexion.close()
    
    col1, col2 = st.columns(2)
    with col1: st.metric("🥘 Comida Consumida (Acumulado)", f"{coste_comida:,.2f} €")
    with col2: st.metric("🗑️ Mermas / Tirado", f"{mermas_comida:,.2f} €")
        
    with st.form("form_despensa_manual", clear_on_submit=True):
        st.subheader("➕ Añadir Regalo o Stock a Coste Cero")
        st.caption("Usa esto para tuppers, regalos o comida que no afectará a tus gastos.")
        col_d1, col_d2, col_d3 = st.columns(3)
        with col_d1: 
            nombre_d = st.text_input("Producto")
        with col_d2: 
            ubicacion_d = st.selectbox("Ubicación", ["Armario", "Nevera", "Congelador"])
        with col_d3:
            unidades_d = st.number_input("Unidades", min_value=1, step=1)
            
        if st.form_submit_button("Añadir al Inventario") and nombre_d:
            conexion_w = init_connection()
            cursor_w = conexion_w.cursor()
            cursor_w.execute("INSERT INTO despensa (producto_generico, supermercado, unidades_actuales, peso_neto_kg, precio_unitario, fecha_compra, ubicacion) VALUES (%s, 'Regalo/Sin Coste', %s, 1.0, 0.0, %s, %s)",
                           (nombre_d.strip().lower(), unidades_d, datetime.now().strftime("%Y-%m-%d"), ubicacion_d))
            conexion_w.commit()
            conexion_w.close()
            st.rerun()

    st.markdown("---")
    st.header("📦 Existencias (Alimentos)")
    
    if not df_stock.empty:
        if 'ubicacion' not in df_stock.columns:
            df_stock['ubicacion'] = 'Armario'
        else:
            df_stock['ubicacion'] = df_stock['ubicacion'].fillna("Armario")
            
        filtro_vista = st.radio("🔍 Filtrar por ubicación:", ["Mostrar Todo", "Armario", "Nevera", "Congelador"], horizontal=True)
        df_filtrado = df_stock if filtro_vista == "Mostrar Todo" else df_stock[df_stock['ubicacion'] == filtro_vista]

        if df_filtrado.empty:
            st.info(f"Vaya, parece que no tienes ningún alimento en: {filtro_vista}.")
        else:
            iconos_ubi = {"Armario": "🚪", "Nevera": "🧊", "Congelador": "❄️"}
            
            for index, fila in df_filtrado.iterrows():
                id_prod = fila['id']
                nombre = fila['producto_generico'].capitalize()
                superm = fila['supermercado']
                cant = fila['unidades_actuales']
                peso = fila['peso_neto_kg']
                precio = fila['precio_unitario']
                ubi_actual = fila['ubicacion']
                
                txt_precio = f" | {precio}€/ud" if precio > 0 else " | 🎁 Sin coste"
                
                c_info, c_ubi, c_btn1, c_btn2, c_btn3, c_btn4 = st.columns([3, 1.5, 0.8, 0.8, 0.8, 1.2])
                with c_info: 
                    icono = iconos_ubi.get(ubi_actual, "📦")
                    st.write(f"{icono} **{nombre}** ({superm}) — **{cant} uds**{txt_precio}")
                    
                with c_ubi:
                    nueva_ubi = st.selectbox("Lugar", ["Armario", "Nevera", "Congelador"], index=["Armario", "Nevera", "Congelador"].index(ubi_actual), key=f"ubi_{id_prod}", label_visibility="collapsed")
                    if nueva_ubi != ubi_actual:
                        conexion_u = init_connection()
                        cursor_u = conexion_u.cursor()
                        cursor_u.execute("UPDATE despensa SET ubicacion = %s WHERE id = %s", (nueva_ubi, id_prod))
                        conexion_u.commit()
                        conexion_u.close()
                        st.rerun()
                        
                with c_btn1:
                    if st.button(f"🍽️", key=f"con_{id_prod}", help="Consumir 1 ud"):
                        conexion_u = init_connection()
                        cursor_u = conexion_u.cursor()
                        cursor_u.execute("UPDATE despensa SET unidades_actuales = unidades_actuales - 1 WHERE id = %s", (id_prod,))
                        cursor_u.execute("INSERT INTO consumo_alimentos (fecha, producto_generico, cantidad, coste_estimado, estado) VALUES (%s, %s, 1, %s, 'Consumido')", (datetime.now().strftime("%Y-%m-%d"), nombre.lower(), precio))
                        conexion_u.commit()
                        conexion_u.close()
                        
                        if cant - 1 == 0 and precio > 0: 
                            mejor_super = obtener_mejor_super(nombre, tabla="despensa")
                            añadir_a_lista_compra(nombre.lower(), mejor_super)
                        st.rerun()
                        
                with c_btn2:
                    if st.button(f"🗑️", key=f"tir_{id_prod}", help="Tirar a la basura (Añade a Mermas)"):
                        conexion_u = init_connection()
                        cursor_u = conexion_u.cursor()
                        cursor_u.execute("UPDATE despensa SET unidades_actuales = unidades_actuales - 1 WHERE id = %s", (id_prod,))
                        cursor_u.execute("INSERT INTO consumo_alimentos (fecha, producto_generico, cantidad, coste_estimado, estado) VALUES (%s, %s, 1, %s, 'Tirado')", (datetime.now().strftime("%Y-%m-%d"), nombre.lower(), precio))
                        conexion_u.commit()
                        conexion_u.close()
                        
                        if cant - 1 == 0 and precio > 0: 
                            mejor_super = obtener_mejor_super(nombre, tabla="despensa")
                            añadir_a_lista_compra(nombre.lower(), mejor_super)
                        st.rerun()

                with c_btn3:
                    if st.button(f"❌", key=f"del_{id_prod}", help="Corregir error (Borra sin dejar rastro)"):
                        conexion_u = init_connection()
                        cursor_u = conexion_u.cursor()
                        cursor_u.execute("UPDATE despensa SET unidades_actuales = unidades_actuales - 1 WHERE id = %s", (id_prod,))
                        conexion_u.commit()
                        conexion_u.close()
                        st.rerun()
                        
                with c_btn4:
                    if st.button(f"➡️ Hogar", key=f"mov_h_{id_prod}", help="Mover este artículo a Utensilios (Hogar)"):
                        conexion_u = init_connection()
                        cursor_u = conexion_u.cursor()
                        cursor_u.execute("INSERT INTO utensilios (producto_generico, supermercado, unidades_actuales, peso_neto_kg, precio_unitario, fecha_compra) VALUES (%s, %s, %s, %s, %s, %s)",
                                       (nombre.lower(), superm, cant, peso, precio, fila['fecha_compra']))
                        cursor_u.execute("DELETE FROM despensa WHERE id = %s", (id_prod,))
                        cursor_u.execute("UPDATE movimientos_caja SET concepto = %s WHERE concepto = %s AND fecha = %s",
                                       (f"Bazar/Utensilio: {nombre}", f"Alimento: {nombre}", fila['fecha_compra']))
                        conexion_u.commit()
                        conexion_u.close()
                        st.rerun()

            st.markdown("<hr style='margin:0.2rem 0px;'/>", unsafe_allow_html=True)
    else: 
        st.info("No hay alimentos en la despensa.")

elif opcion_menu == "🏠 Utensilios (Hogar)":
    st.title("🏠 Inventario de Utensilios y Limpieza")
    
    conexion = init_connection()
    cursor = conexion.cursor()
    cursor.execute("SELECT COALESCE(SUM(unidades_actuales * precio_unitario), 0) FROM utensilios")
    inmovilizado_hogar = cursor.fetchone()[0]
    utensilios = pd.read_sql_query("SELECT * FROM utensilios WHERE unidades_actuales > 0", conexion)
    conexion.close()
    
    st.metric(label="🧴 Valor Inmovilizado en Hogar", value=f"{inmovilizado_hogar:,.2f} €")
    
    with st.form("form_utensilios_manual", clear_on_submit=True):
        st.subheader("➕ Añadir stock manual (Sin registrar pago)")
        col1, col2, col3 = st.columns([4, 3, 3])
        with col1: nombre_u = st.text_input("Producto")
        with col2: super_u = st.selectbox("Origen", LISTA_SUPERS)
        with col3:
            unidades_u = st.number_input("Unidades", min_value=1, step=1)
            precio_u = st.number_input("Precio/Ud aprox (€)", min_value=0.0, step=0.1)
            
        if st.form_submit_button("Añadir al Inventario") and nombre_u:
            conexion_w = init_connection()
            cursor_w = conexion_w.cursor()
            cursor_w.execute("INSERT INTO utensilios (producto_generico, supermercado, unidades_actuales, peso_neto_kg, precio_unitario, fecha_compra) VALUES (%s, %s, %s, 1.0, %s, %s)",
                           (nombre_u.strip().lower(), super_u, unidades_u, precio_u, datetime.now().strftime("%Y-%m-%d")))
            conexion_w.commit()
            conexion_w.close()
            st.rerun()
            
    st.markdown("---")
    st.header("📦 Existencias (Hogar)")
    
    if not utensilios.empty:
        for index, fila in utensilios.iterrows():
            id_prod = fila['id']
            nombre = fila['producto_generico'].capitalize()
            superm = fila['supermercado']
            cant = fila['unidades_actuales']
            precio = fila['precio_unitario']
            
            c1, c2, c3, c4 = st.columns([5, 1.5, 1.5, 1.5])
            with c1: st.write(f"🔹 **{nombre}** ({superm}) — **{cant} uds** | **{precio} €/ud**")
            with c2:
                if st.button("🧹 Gastar 1 ud", key=f"uso_hogar_{id_prod}"):
                    conexion_u = init_connection()
                    cursor_u = conexion_u.cursor()
                    cursor_u.execute("UPDATE utensilios SET unidades_actuales = unidades_actuales - 1 WHERE id = %s", (id_prod,))
                    conexion_u.commit()
                    conexion_u.close()
                    
                    if cant - 1 == 0: 
                        mejor_super = obtener_mejor_super(nombre, tabla="utensilios")
                        añadir_a_lista_compra(nombre.lower(), mejor_super)
                    st.rerun()
            with c3:
                if st.button("🗑️ Desechar", key=f"tir_hogar_{id_prod}"):
                    conexion_u = init_connection()
                    cursor_u = conexion_u.cursor()
                    cursor_u.execute("UPDATE utensilios SET unidades_actuales = unidades_actuales - 1 WHERE id = %s", (id_prod,))
                    conexion_u.commit()
                    conexion_u.close()
                    
                    if cant - 1 == 0: 
                        mejor_super = obtener_mejor_super(nombre, tabla="utensilios")
                        añadir_a_lista_compra(nombre.lower(), mejor_super)
                    st.rerun()
            with c4:
                if st.button("➡️ Despensa", key=f"mov_d_{id_prod}", help="Mover este artículo a Despensa (Alimentos)"):
                    conexion_u = init_connection()
                    cursor_u = conexion_u.cursor()
                    cursor_u.execute("INSERT INTO despensa (producto_generico, supermercado, unidades_actuales, peso_neto_kg, precio_unitario, fecha_compra, ubicacion) VALUES (%s, %s, %s, %s, %s, %s, 'Armario')",
                                   (nombre.lower(), superm, cant, fila['peso_neto_kg'], precio, fila['fecha_compra']))
                    cursor_u.execute("DELETE FROM utensilios WHERE id = %s", (id_prod,))
                    cursor_u.execute("UPDATE movimientos_caja SET concepto = %s WHERE concepto = %s AND fecha = %s",
                                   (f"Alimento: {nombre}", f"Bazar/Utensilio: {nombre}", fila['fecha_compra']))
                    conexion_u.commit()
                    conexion_u.close()
                    st.rerun()

            st.markdown("<hr style='margin:0.2rem 0px;'/>", unsafe_allow_html=True)
    else: 
        st.info("No tienes utensilios o productos de limpieza registrados.")

elif opcion_menu == "🛒 Lista de la Compra":
    st.title("🛒 Lista de la Compra Inteligente")
    
    with st.form("form_lista_manual", clear_on_submit=True):
        col1, col2, col3 = st.columns([5, 3, 2])
        with col1: prod_manual = st.text_input("Añadir producto suelto")
        with col2: super_manual = st.selectbox("Supermercado", LISTA_SUPERS)
        with col3: 
            st.write("")
            if st.form_submit_button("Añadir a la Lista") and prod_manual:
                añadir_a_lista_compra(prod_manual.capitalize(), super_manual)
                st.rerun()
                
    st.markdown("---")
    
    conexion = init_connection()
    lista_df = pd.read_sql_query("SELECT * FROM lista_compra ORDER BY supermercado_recomendado ASC", conexion)
    conexion.close()
    
    if not lista_df.empty:
        texto_export = "LISTA DE LA COMPRA\n====================\n\n"
        
        for superm in sorted(lista_df['supermercado_recomendado'].unique()):
            st.subheader(f"🏪 {superm.upper()}")
            texto_export += f"--- {superm.upper()} ---\n"
            
            productos_super = lista_df[lista_df['supermercado_recomendado'] == superm]
            for _, row in productos_super.iterrows():
                c1, c2 = st.columns([9, 1])
                with c1: st.write(f"▫️ {row['producto'].capitalize()}")
                with c2: 
                    if st.button("❌", key=f"del_list_{row['id']}"):
                        conexion_w = init_connection()
                        cursor_w = conexion_w.cursor()
                        cursor_w.execute("DELETE FROM lista_compra WHERE id=%s", (row['id'],))
                        conexion_w.commit()
                        conexion_w.close()
                        st.rerun()
                texto_export += f"[ ] {row['producto'].capitalize()}\n"
            texto_export += "\n"
            st.markdown("<hr style='margin:0.2rem 0px;'/>", unsafe_allow_html=True)
            
        st.markdown("<br>", unsafe_allow_html=True)
        
        def limpiar_bd_lista():
            conn_limpia = init_connection()
            cursor_l = conn_limpia.cursor()
            cursor_l.execute("DELETE FROM lista_compra")
            conn_limpia.commit()
            conn_limpia.close()

        st.download_button(
            label="📥 Descargar TXT para el Móvil y Limpiar Lista", 
            data=texto_export, 
            file_name="Lista_Compra.txt", 
            mime="text/plain", 
            on_click=limpiar_bd_lista
        )
    else: 
        st.success("¡Tu lista de la compra está vacía!")

elif opcion_menu == "🔄 Gastos Recurrentes":
    st.title("🔄 Gestión de Gastos Fijos")
    col_izq, col_der = st.columns([4, 6])
    
    with col_izq:
        with st.form("form_add_recurrente", clear_on_submit=True):
            nombre_fijo = st.text_input("Nombre del Gasto").strip()
            monto_fijo = st.number_input("Importe Mensual (€)", min_value=0.1, step=5.0)
            if st.form_submit_button("Registrar") and nombre_fijo and monto_fijo > 0:
                try:
                    conexion_w = init_connection()
                    cursor_w = conexion_w.cursor()
                    cursor_w.execute("INSERT INTO gastos_recurrentes (nombre_gasto, monto) VALUES (%s, %s)", (nombre_fijo, monto_fijo))
                    conexion_w.commit()
                    conexion_w.close()
                    st.rerun()
                except IntegrityError: 
                    st.error("⚠️ Ya existe ese gasto.")
                    
    with col_der:
        conexion = init_connection()
        df_fijos = pd.read_sql_query("SELECT * FROM gastos_recurrentes", conexion)
        conexion.close()
        for index, fila in df_fijos.iterrows():
            c_txt, c_btn = st.columns([8, 2])
            with c_txt: st.write(f"💼 **{fila['nombre_gasto']}**: {fila['monto']:,.2f} € / mes")
            with c_btn:
                if st.button("🗑️ Eliminar", key=f"del_rec_{fila['id']}"):
                    conexion_w = init_connection()
                    cursor_w = conexion_w.cursor()
                    cursor_w.execute("DELETE FROM movimientos_caja WHERE concepto = %s", (f"Fijo Automático: {fila['nombre_gasto']}",))
                    cursor_w.execute("DELETE FROM gastos_recurrentes WHERE id = %s", (fila['id'],))
                    conexion_w.commit()
                    conexion_w.close()
                    st.rerun()

elif opcion_menu == "💳 Compras a Plazos":
    st.title("💳 Auditoría de Compras Financiadas a Plazos")
    col_l, col_r = st.columns([4, 6])
    
    with col_l:
        with st.form("form_plazos", clear_on_submit=True):
            art_nombre = st.text_input("Artículo").strip()
            art_total = st.number_input("Coste Total (€)", min_value=1.0, step=50.0)
            art_meses = st.number_input("Meses", min_value=1, step=1, value=12)
            if st.form_submit_button("Auditar y Registrar"):
                cuota = art_total / art_meses
                if cuota < 25.0: 
                    st.error(f"🚨 Cuota de {cuota:.2f} €/mes. Regla rota. ¡A tocateja!")
                else:
                    try:
                        conexion_w = init_connection()
                        cursor_w = conexion_w.cursor()
                        cursor_w.execute("INSERT INTO compras_plazos (articulo, monto_total, meses_totales, meses_pagados) VALUES (%s, %s, %s, 0)", (art_nombre, art_total, art_meses))
                        conexion_w.commit()
                        conexion_w.close()
                        st.rerun()
                    except IntegrityError: 
                        st.error("Ya existe.")
                        
    with col_r:
        conexion = init_connection()
        df_plazos = pd.read_sql_query("SELECT * FROM compras_plazos", conexion)
        conexion.close()
        for index, fila in df_plazos.iterrows():
            cuota_actual = fila['monto_total'] / fila['meses_totales']
            st.write(f"💳 **{fila['articulo']}** | Cuota: **{cuota_actual:.2f} €/mes**")
            st.progress((fila['meses_pagados'] / fila['meses_totales']))
            if st.button("🗑️ Eliminar", key=f"del_plazo_{fila['id']}"):
                conexion_w = init_connection()
                cursor_w = conexion_w.cursor()
                cursor_w.execute("DELETE FROM movimientos_caja WHERE concepto LIKE %s", (f"Cuota Plazo: {fila['articulo']} (%%",))
                cursor_w.execute("DELETE FROM compras_plazos WHERE id = %s", (fila['id'],))
                conexion_w.commit()
                conexion_w.close()
                st.rerun()

elif opcion_menu == "🗓️ Previsiones Anuales":
    st.title("🗓️ Gestor de Provisiones (Sinking Funds)")
    st.info("💡 Crea 'huchas virtuales' para pagos grandes (Seguro, IBI, Taller). El sistema dividirá el coste entre 12 y apartará esa cuota cada mes para que el pago no te pille por sorpresa.")
    
    col_p1, col_p2 = st.columns([4, 6])
    
    with col_p1:
        with st.form("form_nueva_prevision", clear_on_submit=True):
            st.subheader("➕ Nueva Previsión")
            prev_concepto = st.text_input("Concepto (Ej. Seguro Coche, IBI)").strip()
            prev_monto = st.number_input("Coste Anual Estimado (€)", min_value=10.0, step=50.0)
            prev_mes = st.selectbox("Mes de Cobro", [1,2,3,4,5,6,7,8,9,10,11,12], format_func=lambda x: ["Enero","Febrero","Marzo","Abril","Mayo","Junio","Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"][x-1])
            
            if st.form_submit_button("Crear Previsión Anual") and prev_concepto:
                try:
                    conexion_w = init_connection()
                    cursor_w = conexion_w.cursor()
                    cursor_w.execute("INSERT INTO previsiones_anuales (concepto, monto_total, mes_objetivo) VALUES (%s, %s, %s)", (prev_concepto, prev_monto, prev_mes))
                    conexion_w.commit()
                    conexion_w.close()
                    st.success("Regla de provisión creada.")
                    st.rerun()
                except IntegrityError:
                    st.error("⚠️ Ya existe una previsión con ese nombre.")
                    
    with col_p2:
        st.subheader("🛡️ Tus Provisiones Activas")
        conexion = init_connection()
        df_prevs = pd.read_sql_query("SELECT * FROM previsiones_anuales ORDER BY mes_objetivo ASC", conexion)
        conexion.close()
        
        meses_nombres = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
        
        if not df_prevs.empty:
            for index, fila in df_prevs.iterrows():
                cuota_mes = fila['monto_total'] / 12.0
                nombre_mes = meses_nombres[fila['mes_objetivo'] - 1]
                
                c_txt, c_pago, c_del = st.columns([6, 3, 1])
                with c_txt:
                    st.write(f"🎯 **{fila['concepto']}** | Objetivo: **{fila['monto_total']:,.2f} €** (en {nombre_mes})")
                    st.caption(f"🛡️ Guardando: **{cuota_mes:.2f} € / mes**")
                with c_pago:
                    if st.button("💳 Registrar Pago", key=f"pagar_prev_{fila['id']}", help="Extrae el dinero del banco hoy, pero mantiene la regla de ahorro para el año que viene."):
                        conexion_w = init_connection()
                        cursor_w = conexion_w.cursor()
                        fecha_actual = datetime.now().strftime("%Y-%m-%d")
                        cursor_w.execute("INSERT INTO movimientos_caja (fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago) VALUES (%s, %s, %s, 'Gasto Habitual', 'Tarjeta/PayPal')", 
                                         (fecha_actual, f"Pago Previsión: {fila['concepto']}", fila['monto_total']))
                        conexion_w.commit()
                        conexion_w.close()
                        st.success(f"Cobro de {fila['monto_total']}€ registrado en tu Banco.")
                        st.rerun()
                with c_del:
                    if st.button("❌", key=f"del_prev_{fila['id']}"):
                        conexion_w = init_connection()
                        cursor_w = conexion_w.cursor()
                        cursor_w.execute("DELETE FROM previsiones_anuales WHERE id = %s", (fila['id'],))
                        conexion_w.commit()
                        conexion_w.close()
                        st.rerun()
                st.markdown("<hr style='margin:0.2rem 0px;'/>", unsafe_allow_html=True)
        else:
            st.info("No tienes gastos anuales previstos. ¡Configura tu seguro o impuestos aquí!")

elif opcion_menu == "🔮 Previsiones y Proyectos":
    st.title("🔮 Consultor de Viabilidad y Airbag")
    st.caption("Descubre cuánto dinero libre tienes realmente y blinda tu economía ante imprevistos.")
    
    conexion = init_connection()
    cursor = conexion.cursor()
    
    cursor.execute("SELECT COALESCE(SUM(monto), 0) FROM gastos_recurrentes")
    total_recurrentes = cursor.fetchone()[0]
    
    cursor.execute("SELECT monto_total, meses_totales FROM compras_plazos WHERE meses_pagados < meses_totales")
    total_cuotas_plazos = sum(row[0] / row[1] for row in cursor.fetchall())
    
    cursor.execute("SELECT COALESCE(SUM(monto_total / 12.0), 0) FROM previsiones_anuales")
    total_provisiones_mes = cursor.fetchone()[0]
    
    df_supermercado = pd.read_sql_query("""
        SELECT fecha, monto FROM movimientos_caja 
        WHERE tipo_ingreso_gasto IN ('Alimentación', 'Hogar') 
           OR concepto LIKE 'Alimento:%' 
           OR concepto LIKE 'Bazar/Utensilio:%'
    """, conexion)
    
    df_airbag = pd.read_sql_query("SELECT acumulado FROM fondo_emergencia WHERE id=1", conexion)
    acumulado_airbag = df_airbag.iloc[0]['acumulado'] if not df_airbag.empty else 0.0
    
    df_proj = pd.read_sql_query("SELECT * FROM proyectos_futuros", conexion)
    
    conexion.close()

    if not df_supermercado.empty:
        df_supermercado['mes_año'] = df_supermercado['fecha'].str[:7]
        meses_registrados = df_supermercado['mes_año'].nunique()
        gasto_total_historico = df_supermercado['monto'].sum()
        media_supermercado = gasto_total_historico / meses_registrados if meses_registrados > 0 else 0.0
    else:
        media_supermercado = 0.0

    total_cuotas_proyectos = 0.0
    for index, fila in df_proj.iterrows():
        faltan = fila['objetivo_total'] - fila['ahorrado_acumulado']
        if faltan > 0:
            total_cuotas_proyectos += faltan / fila['meses_restantes'] if fila['meses_restantes'] > 0 else faltan

    col_p1, col_p2 = st.columns(2)
    with col_p1: sueldo_base = st.number_input("Nómina Fija Mensual (€)", min_value=0.0, value=1300.0)
    with col_p2: gastos_fijos_est = st.number_input("Suministros (Agua, Luz, Internet, etc)", min_value=0.0, value=0.0)
        
    coste_supervivencia = gastos_fijos_est + total_recurrentes + total_cuotas_plazos + total_provisiones_mes + media_supermercado
    objetivo_airbag = coste_supervivencia * 2.5
    
    capacidad_ahorroador_teorica = sueldo_base - coste_supervivencia
    ahorro_libre_real = capacidad_ahorroador_teorica - total_cuotas_proyectos
    
    st.markdown("---")
    st.header("🛡️ Tu Airbag Financiero (Fondo de Emergencia)")
    st.info(f"💡 **Coste de Supervivencia:** Tu casa necesita **{coste_supervivencia:,.2f} €/mes** para funcionar. Tu objetivo ideal es acumular 2.5 meses de tranquilidad (**{objetivo_airbag:,.2f} €**).")
    
    progreso_airbag = min(100.0, (acumulado_airbag / objetivo_airbag) * 100) if objetivo_airbag > 0 else 100.0
    
    col_a1, col_a2, col_a3 = st.columns([6, 2, 2])
    with col_a1:
        st.progress(progreso_airbag / 100.0)
        st.write(f"Estado del Airbag: **{acumulado_airbag:,.2f} €** / {objetivo_airbag:,.2f} € ({progreso_airbag:.1f}%)")
    with col_a2:
        abono_airbag = st.number_input("Mover al Airbag (€)", min_value=0.0, step=50.0)
    with col_a3:
        st.write("") 
        if st.button("🛡️ Blindar Dinero") and abono_airbag > 0:
            conexion_w = init_connection()
            cursor_w = conexion_w.cursor()
            cursor_w.execute("UPDATE fondo_emergencia SET acumulado = acumulado + %s WHERE id = 1", (abono_airbag,))
            cursor_w.execute("INSERT INTO movimientos_caja (fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago) VALUES (%s, %s, %s, 'Gasto Habitual', 'Tarjeta/PayPal')", 
                             (datetime.now().strftime("%Y-%m-%d"), "Abono: Fondo de Emergencia", abono_airbag))
            conexion_w.commit()
            conexion_w.close()
            st.rerun()

    st.markdown("---")
    
    st.success(f"### 💰 AHORRO LIBRE DISPONIBLE PARA NUEVOS PROYECTOS: {ahorro_libre_real:,.2f} € / mes")
    
    st.markdown("**(Desglose Analítico):**")
    st.markdown(f"➕ Nómina: `{sueldo_base:,.2f} €`")
    st.markdown(f"➖ Coste de Supervivencia (Suministros, Letras, Comida, Seguros): `{coste_supervivencia:,.2f} €`")
    st.markdown(f"➖ Cuotas comprometidas en Proyectos Activos: `{total_cuotas_proyectos:,.2f} €`")
    st.markdown("---")
    
    if progreso_airbag < 100.0:
        st.warning("⚠️ **Atención:** Tu Airbag Financiero aún no está lleno. Te recomendamos encarecidamente priorizar este fondo antes de lanzar proyectos de capricho.")
    
    with st.form("form_proyecto", clear_on_submit=True):
        st.subheader("🚀 Lanzar un Proyecto Finalista (Ej. Congelador, Viaje)")
        c1, c2, c3 = st.columns(3)
        with c1: proj_name = st.text_input("Proyecto")
        with c2: proj_target = st.number_input("Objetivo (€)", min_value=10.0)
        with c3: proj_months = st.number_input("Meses para lograrlo", min_value=1, step=1)
        
        if st.form_submit_button("Consultar Viabilidad y Lanzar") and proj_name:
            cuota_necesaria = proj_target / proj_months if proj_months > 0 else proj_target
            
            if cuota_necesaria > ahorro_libre_real:
                st.error(f"🚨 INVIABLE: Este proyecto requiere ahorrar **{cuota_necesaria:.2f} €/mes**. Tu Ahorro Libre Disponible es de solo **{ahorro_libre_real:.2f} €/mes**.")
            else:
                try:
                    conexion_w = init_connection()
                    cursor_w = conexion_w.cursor()
                    cursor_w.execute("INSERT INTO proyectos_futuros (nombre_proyecto, objetivo_total, meses_restantes) VALUES (%s, %s, %s)", (proj_name, proj_target, proj_months))
                    conexion_w.commit()
                    conexion_w.close()
                    st.rerun()
                except IntegrityError: 
                    st.error("⚠️ Ya existe un proyecto con ese nombre.")
                
    for index, fila in df_proj.iterrows():
        faltan = fila['objetivo_total'] - fila['ahorrado_acumulado']
        progreso = min(100.0, (fila['ahorrado_acumulado'] / fila['objetivo_total']) * 100)
        
        st.subheader(f"🎯 Proyecto: {fila['nombre_proyecto']}")
        st.progress(progreso / 100.0)
        
        if progreso >= 100.0:
            st.success("✅ ¡Objetivo conseguido! Ya tienes el dinero protegido. Ve a la tienda, compra tu capricho y cierra este proyecto.")
            if st.button("🎉 Comprar y Cerrar Proyecto", key=f"fin_proj_{fila['id']}"):
                conexion_w = init_connection()
                cursor_w = conexion_w.cursor()
                cursor_w.execute("DELETE FROM proyectos_futuros WHERE id = %s", (fila['id'],))
                conexion_w.commit()
                conexion_w.close()
                st.rerun()
        else:
            cuota_sugerida = faltan / fila['meses_restantes'] if fila['meses_restantes'] > 0 else faltan
            st.caption(f"Faltan **{faltan:,.2f} €** | Cuota ideal recomendada: **{cuota_sugerida:,.2f} €/mes**")
            
            col_add1, col_add2, col_add3 = st.columns([2, 5, 3])
            with col_add1: abono = st.number_input(f"Abonar (€)", min_value=0.0, step=10.0, key=f"num_{fila['id']}")
            with col_add2:
                st.write("")
                if st.button("Confirmar", key=f"btn_h_{fila['id']}") and abono > 0:
                    conexion_w = init_connection()
                    cursor_w = conexion_w.cursor()
                    cursor_w.execute("UPDATE proyectos_futuros SET ahorrado_acumulado = ahorrado_acumulado + %s WHERE id = %s", (abono, fila['id']))
                    cursor_w.execute("INSERT INTO movimientos_caja (fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago) VALUES (%s, %s, %s, 'Gasto Habitual', 'Tarjeta/PayPal')", 
                                     (datetime.now().strftime("%Y-%m-%d"), f"Abono hucha: {fila['nombre_proyecto']}", abono))
                    conexion_w.commit()
                    conexion_w.close()
                    st.rerun()
            with col_add3:
                st.write("")
                if st.button("🗑️ Cancelar Proyecto", key=f"del_proj_{fila['id']}", help="Cancela el proyecto y devuelve el dinero ahorrado a tu Bolsa Única."):
                    conexion_w = init_connection()
                    cursor_w = conexion_w.cursor()
                    cursor_w.execute("DELETE FROM movimientos_caja WHERE concepto = %s", (f"Abono hucha: {fila['nombre_proyecto']}",))
                    cursor_w.execute("DELETE FROM proyectos_futuros WHERE id = %s", (fila['id'],))
                    conexion_w.commit()
                    conexion_w.close()
                    st.rerun()
        st.markdown("<hr style='margin:0.5rem 0px;'/>", unsafe_allow_html=True)

elif opcion_menu == "🚗 Mi Coche":
    st.title("🚗 Dashboard del Vehículo")
    
    conexion = init_connection()
    config_coche = pd.read_sql_query("SELECT letra_mensual FROM configuracion_coche WHERE id=1", conexion)
    letra_val = config_coche.iloc[0]['letra_mensual'] if not config_coche.empty else 0.0
    
    mes_actual = datetime.now().strftime("%Y-%m")
    variables_df = pd.read_sql_query("SELECT * FROM movimientos_caja WHERE tipo_ingreso_gasto = 'Gasto Coche' AND fecha LIKE %s", conexion, params=(f"{mes_actual}%",))
    conexion.close()
    
    gasto_var_total = variables_df['monto'].sum() if not variables_df.empty else 0.0
    coste_total_mes = letra_val + gasto_var_total
    
    st.info("💡 **TCO Mensual (Coste Total de Propiedad):** Esta es la radiografía de tu letra y consumo este mes. (Los seguros anuales se gestionan ahora desde '🗓️ Previsiones Anuales').")
    col1, col2, col3 = st.columns(3)
    with col1: st.metric("🔥 Coste Total este Mes", f"{coste_total_mes:,.2f} €")
    with col2: st.metric("🏦 Letra (Fijo)", f"{letra_val:,.2f} €")
    with col3: st.metric("⛽ Variables (Gas, Lavado)", f"{gasto_var_total:,.2f} €")
    
    st.markdown("---")
    col_izq, col_der = st.columns(2)
    
    with col_izq:
        st.header("⚙️ Configuración Fija")
        with st.form("form_coche_fijos", clear_on_submit=True):
            st.caption("Ajusta tu letra mensual para el cálculo estadístico.")
            nueva_letra = st.number_input("Letra del Coche (€/mes)", min_value=0.0, step=10.0, value=float(letra_val))
            
            if st.form_submit_button("Guardar Letra"):
                conexion_w = init_connection()
                cursor_w = conexion_w.cursor()
                cursor_w.execute("UPDATE configuracion_coche SET letra_mensual=%s WHERE id=1", (nueva_letra,))
                conexion_w.commit()
                conexion_w.close()
                st.success("Letra del coche actualizada.")
                st.rerun()
                
    with col_der:
        st.header("⛽ Añadir Gasto Variable")
        with st.form("form_coche_var", clear_on_submit=True):
            st.caption("Estos gastos se restarán inmediatamente de tu Banco o Efectivo.")
            tipo_gasto_coche = st.selectbox("Categoría", ["Gasolina", "Lavadero / Limpieza", "Taller / Mantenimiento", "Peaje / Parking", "Otro"])
            monto_coche = st.number_input("Importe (€)", min_value=0.0, step=5.0)
            metodo_coche = st.selectbox("Pago", ["Tarjeta/PayPal", "Efectivo"])
            
            if st.form_submit_button("Registrar Gasto") and monto_coche > 0:
                fecha_actual = datetime.now().strftime("%Y-%m-%d")
                conexion_w = init_connection()
                cursor_w = conexion_w.cursor()
                cursor_w.execute("INSERT INTO movimientos_caja (fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago) VALUES (%s, %s, %s, 'Gasto Coche', %s)",
                               (fecha_actual, f"Coche: {tipo_gasto_coche}", monto_coche, metodo_coche))
                conexion_w.commit()
                conexion_w.close()
                st.rerun()
                
    st.markdown("---")
    st.subheader("📜 Historial de Variables (Este Mes)")
    if not variables_df.empty:
        for _, row in variables_df.iloc[::-1].iterrows():
            c1, c2 = st.columns([9, 1])
            with c1: st.write(f"📅 **{row['fecha']}** | {row['concepto']} -> **{row['monto']:,.2f} €** ({row['metodo_pago']})")
            with c2:
                if st.button("❌", key=f"del_coche_{row['id']}"):
                    conexion_w = init_connection()
                    cursor_w = conexion_w.cursor()
                    cursor_w.execute("DELETE FROM movimientos_caja WHERE id=%s", (row['id'],))
                    conexion_w.commit()
                    conexion_w.close()
                    st.rerun()
            st.markdown("<hr style='margin:0.2rem 0px;'/>", unsafe_allow_html=True)
    else:
        st.info("No has registrado gasolina ni limpiezas este mes.")

elif opcion_menu == "📊 Análisis y Resumen Anual":
    st.title("📊 Análisis Financiero y Resumen Anual")
    st.info("Visualiza la evolución de tus ingresos y gastos a lo largo del tiempo. (Los 'Saldos Iniciales' están excluidos para no distorsionar las métricas reales).")
    
    conexion = init_connection()
    df_movs = pd.read_sql_query("SELECT * FROM movimientos_caja", conexion)
    conexion.close()
    
    if df_movs.empty:
        st.warning("Aún no hay suficientes datos para mostrar analíticas.")
    else:
        df_movs = df_movs[~df_movs['concepto'].str.startswith('Saldo Inicial:')]
        
        if df_movs.empty:
            st.warning("Solo tienes Saldos Iniciales registrados. Empieza a registrar gastos diarios para ver las gráficas.")
        else:
            df_movs['mes'] = df_movs['fecha'].str[:7] 
            
            def clasificar_gasto(row):
                tipo = row['tipo_ingreso_gasto']
                concepto = row['concepto']
                if tipo in ['Ingreso Fijo', 'Ingreso Extra']:
                    return 'Ingresos'
                elif tipo in ['Alimentación', 'Hogar'] or concepto.startswith('Alimento:') or concepto.startswith('Bazar/'):
                    return 'Supermercado (Comida/Hogar)'
                elif tipo == 'Gasto Coche' or concepto.startswith('Pago Previsión: Seguro'):
                    return 'Coche y Transporte'
                elif concepto.startswith('Fijo Automático:') or concepto.startswith('Cuota Plazo:') or concepto.startswith('Pago Previsión:'):
                    return 'Gastos Fijos y Provisiones'
                elif tipo == 'Gasto Excepcional':
                    return 'Excepcionales / Ocio'
                else:
                    return 'Otros Gastos'
            
            df_movs['categoria_informe'] = df_movs.apply(clasificar_gasto, axis=1)
            
            df_gastos = df_movs[df_movs['categoria_informe'] != 'Ingresos']
            df_ingresos = df_movs[df_movs['categoria_informe'] == 'Ingresos']
            
            st.subheader("📉 Evolución de Gastos Mensuales por Categoría")
            if not df_gastos.empty:
                pivot_gastos = df_gastos.pivot_table(index='mes', columns='categoria_informe', values='monto', aggfunc='sum', fill_value=0)
                st.bar_chart(pivot_gastos)
                
                st.subheader("📅 Tabla Contable Detallada")
                pivot_display = pivot_gastos.copy()
                pivot_display['TOTAL MES'] = pivot_display.sum(axis=1)
                st.dataframe(pivot_display.style.format("{:.2f} €"), width='stretch')
            else:
                st.info("No hay gastos registrados para analizar.")
            
            st.markdown("---")
            st.subheader("🏆 Acumulados Históricos (Desde el inicio de la App)")
            col1, col2 = st.columns(2)
            with col1:
                total_gastado = df_gastos['monto'].sum() if not df_gastos.empty else 0.0
                st.metric("Total Dinero Gastado", f"{total_gastado:,.2f} €")
                if not df_gastos.empty:
                    st.write(df_gastos.groupby('categoria_informe')['monto'].sum().sort_values(ascending=False).map("{:,.2f} €".format))
            with col2:
                total_ingresado = df_ingresos['monto'].sum() if not df_ingresos.empty else 0.0
                st.metric("Total Dinero Ingresado", f"{total_ingresado:,.2f} €")

elif opcion_menu == "📷 Lector de Tickets IA":
    st.title("📷 Escáner de Tickets Inteligente")
    api_key = st.secrets.get("GEMINI_API_KEY", "")
    
    if not api_key: 
        st.warning("⚠️ Configura GEMINI_API_KEY en los Secrets de Streamlit.")
    else:
        archivo_ticket = st.file_uploader("Sube la foto del ticket:", type=["jpg", "jpeg", "png"])
        if archivo_ticket:
            imagen = Image.open(archivo_ticket)
            st.image(imagen, width=250)
            
            if st.button("🚀 Analizar Ticket"):
                with st.spinner("Procesando con IA de Alta Precisión..."):
                    try:
                        client = genai.Client(api_key=api_key)
                        imagen.save("temp_ticket.png")
                        
                        prompt = """Analiza este ticket minuciosamente y devuelve ESTRICTAMENTE un objeto JSON.
                        REGLA 1: Clasifica impecablemente los artículos. 'articulos_despensa' es SOLO comida y bebida. 'gastos_hogar' es TODO lo demás (limpieza, detergentes, higiene personal, papel higiénico, menaje, etc.).
                        REGLA 2: NO copies el nombre literal ni abreviaturas raras del ticket. Traduce y resume el producto a su nombre GENÉRICO. Ejemplo: 'detergente gel masella colon 12' -> 'Detergente', 'migas de atun a girasol fyc 650' -> 'Lata de atún', 'pap hig compact' -> 'Papel higiénico'.
                        Las claves del JSON DEBEN ser: supermercado, metodo_pago, articulos_despensa (lista de objetos: producto, unidades, peso_kg, precio_unitario), y gastos_hogar (lista: concepto, precio_total). Sin explicaciones, solo JSON."""
                        
                        uploaded_file = client.files.upload(file="temp_ticket.png")
                        response = client.models.generate_content(model='gemini-2.5-flash', contents=[uploaded_file, prompt])
                        raw_text = response.text.strip()
                        
                        json_marker = chr(96) * 3 + "json"
                        end_marker = chr(96) * 3
                        
                        if json_marker in raw_text:
                            raw_text = raw_text.split(json_marker)[1]
                        if end_marker in raw_text:
                            raw_text = raw_text.rsplit(end_marker, 1)[0]
                            
                        st.session_state['resultado_json_ticket'] = json.loads(raw_text.strip())
                        
                        if os.path.exists("temp_ticket.png"): 
                            os.remove("temp_ticket.png")
                        st.success("¡Análisis completado!")
                    except Exception as e: 
                        st.error(f"Error procesando ticket: {e}")
                    
        if 'resultado_json_ticket' in st.session_state:
            datos = st.session_state['resultado_json_ticket']
            c1, c2 = st.columns(2)
            with c1: super_det = st.selectbox("Supermercado:", LISTA_SUPERS, index=LISTA_SUPERS.index(datos.get('supermercado', 'Otros')) if datos.get('supermercado', 'Otros') in LISTA_SUPERS else 0)
            with c2: pago_det = st.selectbox("Pago:", ["Efectivo", "Tarjeta/PayPal"], index=0 if datos.get('metodo_pago', 'Tarjeta/PayPal') == "Efectivo" else 1)
            
            st.info("💡 **Revisión Manual:** Haz doble clic en cualquier celda para corregir a la IA antes de inyectar. También puedes añadir o borrar filas.")
            
            c_l, c_r = st.columns(2)
            with c_l:
                st.markdown("**🛒 Alimentación**")
                df_desp = pd.DataFrame(datos.get('articulos_despensa', []))
                if not df_desp.empty: 
                    df_desp = st.data_editor(df_desp, width='stretch', num_rows="dynamic", key="edit_desp")
            with c_r:
                st.markdown("**🧼 Hogar / Otros**")
                df_hogar = pd.DataFrame(datos.get('gastos_hogar', []))
                if not df_hogar.empty: 
                    df_hogar = st.data_editor(df_hogar, width='stretch', num_rows="dynamic", key="edit_hogar")
                
            if st.button("🔨 Inyectar Todo al Sistema"):
                conexion_w = init_connection()
                cursor_w = conexion_w.cursor()
                fecha_actual = datetime.now().strftime("%Y-%m-%d")
                
                if not df_desp.empty:
                    for item in df_desp.to_dict('records'):
                        prod_raw = item.get('producto')
                        producto = str(prod_raw).lower().strip() if pd.notna(prod_raw) else 'alimento sin clasificar'
                        
                        unid_raw = item.get('unidades')
                        unidades = int(unid_raw) if pd.notna(unid_raw) else 1
                        
                        peso_raw = item.get('peso_kg')
                        peso = float(peso_raw) if pd.notna(peso_raw) else 1.0
                        
                        precio_raw = item.get('precio_unitario')
                        precio = float(precio_raw) if pd.notna(precio_raw) else 0.0
                        
                        cursor_w.execute("INSERT INTO despensa (producto_generico, supermercado, unidades_actuales, peso_neto_kg, precio_unitario, fecha_compra, ubicacion) VALUES (%s, %s, %s, %s, %s, %s, 'Armario')", 
                                       (producto, super_det, unidades, peso, precio, fecha_actual))
                        cursor_w.execute("INSERT INTO movimientos_caja (fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago) VALUES (%s, %s, %s, 'Gasto Habitual', %s)", 
                                       (fecha_actual, f"Alimento: {producto.capitalize()}", unidades * precio, pago_det))
                
                if not df_hogar.empty:
                    for gasto in df_hogar.to_dict('records'):
                        conc_raw = gasto.get('concepto')
                        concepto_hogar = str(conc_raw).capitalize() if pd.notna(conc_raw) else 'Utensilio desconocido'
                        
                        precio_t_raw = gasto.get('precio_total')
                        precio_total_hogar = float(precio_t_raw) if pd.notna(precio_t_raw) else 0.0
                        
                        cursor_w.execute("INSERT INTO utensilios (producto_generico, supermercado, unidades_actuales, peso_neto_kg, precio_unitario, fecha_compra) VALUES (%s, %s, %s, 1.0, %s, %s)", 
                                       (concepto_hogar.lower(), super_det, precio_total_hogar, fecha_actual))
                        cursor_w.execute("INSERT INTO movimientos_caja (fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago) VALUES (%s, %s, %s, 'Gasto Habitual', %s)", 
                                       (fecha_actual, f"Bazar/Utensilio: {concepto_hogar}", precio_total_hogar, pago_det))
                
                conexion_w.commit()
                conexion_w.close()
                del st.session_state['resultado_json_ticket']
                st.rerun()

elif opcion_menu == "⚙️ Configuración y Arranque":
    st.title("⚙️ Carga de Saldos Iniciales y Mantenimiento")
    st.info("💡 Usa esta pestaña solo para configurar tu punto de partida. Carga lo que tienes en casa hoy. Una vez termines de volcar tu casa, puedes ignorar o borrar esta pestaña.")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.header("💰 1. Dinero Inicial")
        with st.form("form_saldos_iniciales", clear_on_submit=True):
            st.caption("Esto inyectará dinero en el sistema de forma 'fantasma' (no aparecerá en el historial de gastos/ingresos para mantenerlo limpio).")
            saldo_banco_ini = st.number_input("Dinero real en tu Banco (€)", min_value=0.0, step=100.0)
            saldo_hucha_ini = st.number_input("Dinero real en tu Cartera/Hucha (€)", min_value=0.0, step=50.0)
            
            if st.form_submit_button("Cargar Dinero al Sistema"):
                conexion_w = init_connection()
                cursor_w = conexion_w.cursor()
                fecha_actual = datetime.now().strftime("%Y-%m-%d")
                
                if saldo_banco_ini > 0:
                    cursor_w.execute("INSERT INTO movimientos_caja (fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago, subcuenta_extra) VALUES (%s, 'Saldo Inicial: Banco', %s, 'Ingreso Extra', 'Tarjeta/PayPal', 'Extra-Banco')", 
                                     (fecha_actual, saldo_banco_ini))
                if saldo_hucha_ini > 0:
                    cursor_w.execute("INSERT INTO movimientos_caja (fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago, subcuenta_extra) VALUES (%s, 'Saldo Inicial: Efectivo', %s, 'Ingreso Extra', 'Efectivo', 'Extra-Efectivo')", 
                                     (fecha_actual, saldo_hucha_ini))
                
                conexion_w.commit()
                conexion_w.close()
                st.success("Saldos cargados. ¡Ve a Control de Caja y verás la magia!")
                st.rerun()

    with col2:
        st.header("📦 2. Inventario Rápido")
        with st.form("form_inv_rapido", clear_on_submit=True):
            st.caption("Carga artículos a coste 0.00 € y Origen 'Stock Inicial' para no alterar tus estadísticas financieras de consumo de este mes.")
            
            tipo_inv = st.radio("¿Dónde lo guardamos?", ["Despensa", "Hogar"], horizontal=True)
            ubicacion_inv = st.selectbox("Si es Despensa, ¿en qué lugar?", ["Armario", "Nevera", "Congelador"])
            nombre_inv = st.text_input("Nombre del Producto (Ej. Leche, Papel Higiénico)")
            unidades_inv = st.number_input("Cantidad", min_value=1, step=1)
            
            if st.form_submit_button("Cargar a Inventario") and nombre_inv:
                tabla = "despensa" if tipo_inv == "Despensa" else "utensilios"
                conexion_w = init_connection()
                cursor_w = conexion_w.cursor()
                
                if tabla == "despensa":
                    cursor_w.execute(f"INSERT INTO {tabla} (producto_generico, supermercado, unidades_actuales, peso_neto_kg, precio_unitario, fecha_compra, ubicacion) VALUES (%s, 'Stock Inicial', %s, 1.0, 0.0, %s, %s)",
                                   (nombre_inv.strip().lower(), unidades_inv, datetime.now().strftime("%Y-%m-%d"), ubicacion_inv))
                else:
                    cursor_w.execute(f"INSERT INTO {tabla} (producto_generico, supermercado, unidades_actuales, peso_neto_kg, precio_unitario, fecha_compra) VALUES (%s, 'Stock Inicial', %s, 1.0, 0.0, %s)",
                                   (nombre_inv.strip().lower(), unidades_inv, datetime.now().strftime("%Y-%m-%d")))
                
                conexion_w.commit()
                conexion_w.close()
                st.success(f"{unidades_inv}x {nombre_inv.capitalize()} añadidos a tu {tipo_inv}.")
                st.rerun()
                
    st.markdown("---")
    st.header("💣 3. Reset de Fábrica (Pase a Producción)")
    st.caption("Al pulsar este botón, la aplicación destruirá todas las tablas de PostgreSQL y las volverá a crear vírgenes. ¡Úsalo solo para empezar de cero!")
    
    if st.button("🚨 BORRAR TODO Y EMPEZAR DE CERO"):
        try:
            conexion_w = init_connection()
            conexion_w.autocommit = True
            cursor_w = conexion_w.cursor()
            
            tablas = [
                "movimientos_caja", "despensa", "consumo_alimentos", 
                "proyectos_futuros", "gastos_recurrentes", "compras_plazos", 
                "utensilios", "lista_compra", "configuracion_coche", 
                "previsiones_anuales", "fondo_emergencia"
            ]
            for t in tablas:
                cursor_w.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
                
            conexion_w.close()
            
            # ---> ESTA ES LA LÍNEA MÁGICA QUE FALTABA <---
            # Le dice a la caché de Streamlit que se resetee para que vuelva a crear las tablas
            arrancar_sistema.clear()
            
            st.success("¡Base de datos vaporizada en Supabase! La app se está reiniciando...")
            st.rerun()
        except Exception as e:
            st.error(f"Error borrando la base de datos: {e}")
            st.success("¡Base de datos vaporizada en Supabase! La app se está reiniciando...")
            st.rerun()
        except Exception as e:
            st.error(f"Error borrando la base de datos: {e}")
