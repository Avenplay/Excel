import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
from PIL import Image
import json
import os
from google import genai

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

DB_PATH = "economia_casa.db"
LISTA_SUPERS = ["Mercadona", "Lidl", "Carrefour", "Aldi", "Family Cash", "Dia", "Otros"]

# --- INICIALIZACIÓN DE LA BASE DE DATOS ---
def inicializar_base_datos():
    conexion = sqlite3.connect(DB_PATH, timeout=10)
    cursor = conexion.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS movimientos_caja (
        id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT NOT NULL, concepto TEXT NOT NULL,
        monto REAL NOT NULL, tipo_ingreso_gasto TEXT, metodo_pago TEXT, subcuenta_extra TEXT DEFAULT 'N/A'
    )""")
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS despensa (
        id INTEGER PRIMARY KEY AUTOINCREMENT, producto_generico TEXT NOT NULL, supermercado TEXT NOT NULL,
        unidades_actuales INTEGER NOT NULL DEFAULT 0, peso_neto_kg REAL NOT NULL, precio_unitario REAL NOT NULL,
        fecha_compra TEXT
    )""")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS consumo_alimentos (
        id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT NOT NULL, producto_generico TEXT NOT NULL,
        cantidad INTEGER NOT NULL, coste_estimado REAL NOT NULL, estado TEXT
    )""")
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS proyectos_futuros (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nombre_proyecto TEXT NOT NULL UNIQUE,
        objetivo_total REAL NOT NULL, meses_restantes INTEGER NOT NULL, ahorrado_acumulado REAL NOT NULL DEFAULT 0.0
    )""")
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS gastos_recurrentes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nombre_gasto TEXT NOT NULL UNIQUE, monto REAL NOT NULL
    )""")
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS compras_plazos (
        id INTEGER PRIMARY KEY AUTOINCREMENT, articulo TEXT NOT NULL UNIQUE,
        monto_total REAL NOT NULL, meses_totales INTEGER NOT NULL, meses_pagados INTEGER NOT NULL DEFAULT 0
    )""")
    
    try:
        cursor.execute("SELECT precio_unitario FROM utensilios LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("DROP TABLE IF EXISTS utensilios")
        
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS utensilios (
        id INTEGER PRIMARY KEY AUTOINCREMENT, producto_generico TEXT NOT NULL, supermercado TEXT NOT NULL,
        unidades_actuales INTEGER NOT NULL DEFAULT 0, peso_neto_kg REAL NOT NULL, precio_unitario REAL NOT NULL,
        fecha_compra TEXT
    )""")
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS lista_compra (
        id INTEGER PRIMARY KEY AUTOINCREMENT, producto TEXT NOT NULL, supermercado_recomendado TEXT NOT NULL
    )""")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS configuracion_coche (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        letra_mensual REAL NOT NULL DEFAULT 0.0,
        seguro_mensual REAL NOT NULL DEFAULT 0.0
    )""")
    cursor.execute("INSERT OR IGNORE INTO configuracion_coche (id, letra_mensual, seguro_mensual) VALUES (1, 0.0, 0.0)")

    # NUEVA TABLA: PREVISIONES ANUALES
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS previsiones_anuales (
        id INTEGER PRIMARY KEY AUTOINCREMENT, concepto TEXT NOT NULL UNIQUE,
        monto_total REAL NOT NULL, mes_objetivo INTEGER NOT NULL
    )""")

    conexion.commit()
    conexion.close()

# --- PROCESAMIENTO MENSUAL AUTOMÁTICO ---
def ejecutar_automatizaciones_mensuales():
    mes_actual = datetime.now().strftime("%Y-%m")
    conexion = sqlite3.connect(DB_PATH, timeout=10)
    cursor = conexion.cursor()
    
    cursor.execute("SELECT nombre_gasto, monto FROM gastos_recurrentes")
    for nombre, monto in cursor.fetchall():
        concepto_cargo = f"Fijo Automático: {nombre}"
        cursor.execute("SELECT COUNT(*) FROM movimientos_caja WHERE concepto = ? AND fecha LIKE ?", (concepto_cargo, f"{mes_actual}%"))
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO movimientos_caja (fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago) VALUES (?, ?, ?, 'Gasto Habitual', 'Tarjeta/PayPal')", 
                           (datetime.now().strftime("%Y-%m-%d"), concepto_cargo, monto))
            
    cursor.execute("SELECT id, articulo, monto_total, meses_totales, meses_pagados FROM compras_plazos WHERE meses_pagados < meses_totales")
    for pid, articulo, total, m_totales, m_pagados in cursor.fetchall():
        concepto_base = f"Cuota Plazo: {articulo} (%"
        cursor.execute("SELECT COUNT(*) FROM movimientos_caja WHERE concepto LIKE ? AND fecha LIKE ?", (concepto_base, f"{mes_actual}%"))
        if cursor.fetchone()[0] == 0:
            concepto_cuota = f"Cuota Plazo: {articulo} ({m_pagados + 1}/{m_totales})"
            cursor.execute("INSERT INTO movimientos_caja (fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago) VALUES (?, ?, ?, 'Gasto Habitual', 'Tarjeta/PayPal')", 
                           (datetime.now().strftime("%Y-%m-%d"), concepto_cuota, total / m_totales))
            cursor.execute("UPDATE compras_plazos SET meses_pagados = meses_pagados + 1 WHERE id = ?", (pid,))
            
    conexion.commit()
    conexion.close()

inicializar_base_datos()
ejecutar_automatizaciones_mensuales()

# --- FUNCIONES AUXILIARES Y CÁLCULOS ---
def obtener_totales_sistema():
    conexion = sqlite3.connect(DB_PATH, timeout=10)
    ingresos_fijos = pd.read_sql_query("SELECT SUM(monto) FROM movimientos_caja WHERE tipo_ingreso_gasto='Ingreso Fijo'", conexion).iloc[0,0] or 0.0
    extras_banco = pd.read_sql_query("SELECT SUM(monto) FROM movimientos_caja WHERE subcuenta_extra='Extra-Banco'", conexion).iloc[0,0] or 0.0
    gastos_tarjeta = pd.read_sql_query("SELECT SUM(monto) FROM movimientos_caja WHERE tipo_ingreso_gasto LIKE 'Gasto%' AND metodo_pago='Tarjeta/PayPal'", conexion).iloc[0,0] or 0.0
    extras_efectivo = pd.read_sql_query("SELECT SUM(monto) FROM movimientos_caja WHERE subcuenta_extra='Extra-Efectivo'", conexion).iloc[0,0] or 0.0
    gastos_efectivo = pd.read_sql_query("SELECT SUM(monto) FROM movimientos_caja WHERE tipo_ingreso_gasto LIKE 'Gasto%' AND metodo_pago='Efectivo'", conexion).iloc[0,0] or 0.0
    excepcionales = pd.read_sql_query("SELECT SUM(monto) FROM movimientos_caja WHERE tipo_ingreso_gasto='Gasto Excepcional'", conexion).iloc[0,0] or 0.0
    
    coste_comida = pd.read_sql_query("SELECT SUM(coste_estimado) FROM consumo_alimentos WHERE estado='Consumido'", conexion).iloc[0,0] or 0.0
    mermas_comida = pd.read_sql_query("SELECT SUM(coste_estimado) FROM consumo_alimentos WHERE estado='Tirado'", conexion).iloc[0,0] or 0.0
    total_recurrentes = pd.read_sql_query("SELECT SUM(monto) FROM gastos_recurrentes", conexion).iloc[0,0] or 0.0
    
    inmovilizado_comida = pd.read_sql_query("SELECT SUM(unidades_actuales * precio_unitario) FROM despensa", conexion).iloc[0,0] or 0.0
    inmovilizado_hogar = pd.read_sql_query("SELECT SUM(unidades_actuales * precio_unitario) FROM utensilios", conexion).iloc[0,0] or 0.0

    cursor = conexion.cursor()
    cursor.execute("SELECT monto_total, meses_totales FROM compras_plazos WHERE meses_pagados < meses_totales")
    total_cuotas_plazos = sum(row[0] / row[1] for row in cursor.fetchall())
    
    # CÁLCULO DE PROVISIONES (Fondos de Amortización Mensual)
    cursor.execute("SELECT SUM(monto_total / 12.0) FROM previsiones_anuales")
    total_provisiones_mes = cursor.fetchone()[0] or 0.0

    conexion.close()
    
    saldo_b = ingresos_fijos + extras_banco - gastos_tarjeta
    saldo_e = extras_efectivo - gastos_efectivo
    
    return saldo_b, saldo_e, extras_banco, excepcionales, coste_comida, mermas_comida, total_recurrentes, total_cuotas_plazos, inmovilizado_comida, inmovilizado_hogar, total_provisiones_mes

saldo_banco, saldo_efectivo, bizums_bloqueados, excepcionales, coste_comida, mermas_comida, total_recurrentes, total_cuotas_plazos, inmovilizado_comida, inmovilizado_hogar, total_provisiones_mes = obtener_totales_sistema()

def registrar_movimiento(concepto, monto, tipo, metodo, subcuenta='N/A'):
    conexion = sqlite3.connect(DB_PATH, timeout=10)
    cursor = conexion.cursor()
    cursor.execute("INSERT INTO movimientos_caja (fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago, subcuenta_extra) VALUES (?, ?, ?, ?, ?, ?)", 
                   (datetime.now().strftime("%Y-%m-%d"), concepto, monto, tipo, metodo, subcuenta))
    conexion.commit()
    conexion.close()

def obtener_mejor_super(producto_nombre, tabla="despensa"):
    conexion = sqlite3.connect(DB_PATH, timeout=10)
    fecha_limite = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
    q = f"SELECT supermercado FROM {tabla} WHERE producto_generico = ? AND fecha_compra >= ? GROUP BY supermercado ORDER BY AVG(precio_unitario/peso_neto_kg) ASC LIMIT 1"
    res = conexion.execute(q, (producto_nombre.lower().strip(), fecha_limite)).fetchone()
    conexion.close()
    return res[0] if res else "Cualquiera"

def añadir_a_lista_compra(producto, super_rec):
    conexion = sqlite3.connect(DB_PATH, timeout=10)
    ext = conexion.execute("SELECT COUNT(*) FROM lista_compra WHERE producto = ?", (producto,)).fetchone()[0]
    if ext == 0:
        conexion.execute("INSERT INTO lista_compra (producto, supermercado_recomendado) VALUES (?, ?)", (producto, super_rec))
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
    "📷 Lector de Tickets IA",
    "⚙️ Configuración y Arranque"
])

# ==========================================
# VISTAS DE LA APLICACIÓN
# ==========================================
if opcion_menu == "💵 Control de Caja":
    st.title("🧠 Tu Copiloto Financiero Inteligente")
    st.markdown("---")
    
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1: st.metric(label="💳 Bolsa Única", value=f"{saldo_banco:,.2f} €")
    with col2: st.metric(label="💵 Hucha Efectivo", value=f"{saldo_efectivo:,.2f} €")
    # Este es el nuevo indicador de provisiones que pediste
    with col3: st.metric(label="🛡️ Colchón Provisiones", value=f"{total_provisiones_mes:,.2f} €", help="Dinero de tu Bolsa Única que NO debes tocar este mes para poder pagar seguros/IBI a futuro.")
    with col4: st.metric(label="📦 Stock Comida", value=f"{inmovilizado_comida:,.2f} €")
    with col5: st.metric(label="🧴 Stock Hogar", value=f"{inmovilizado_hogar:,.2f} €")
    
    st.markdown("---")
    col_l, col_r = st.columns(2)
    
    with col_l:
        st.header("🛒 Registrar Gasto Manual")
        categoria_gasto = st.selectbox("Categoría del Gasto", ["Alimentación", "Hogar", "Gastos Fijos", "Gasto Excepcional"])
        
        if categoria_gasto in ["Alimentación", "Hogar"]:
            with st.form("form_gasto_inventario"):
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
                    
                    conexion = sqlite3.connect(DB_PATH, timeout=10)
                    cursor = conexion.cursor()
                    
                    cursor.execute("INSERT INTO movimientos_caja (fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago) VALUES (?, ?, ?, ?, ?)", 
                                   (fecha_actual, f"{categoria_gasto}: {concepto_g}", monto_g, "Gasto Habitual", metodo_g))
                    
                    cursor.execute(f"INSERT INTO {tabla_destino} (producto_generico, supermercado, unidades_actuales, peso_neto_kg, precio_unitario, fecha_compra) VALUES (?, ?, ?, ?, ?, ?)", 
                                   (concepto_g.lower().strip(), super_g, unidades_g, peso_g, precio_uni, fecha_actual))
                    
                    conexion.commit()
                    conexion.close()
                    st.rerun()

        elif categoria_gasto == "Gastos Fijos":
            with st.form("form_gasto_fijo"):
                st.caption("Al guardar, se cobrará hoy y se añadirá a la pestaña 'Gastos Recurrentes' para el futuro.")
                concepto_g = st.text_input("Concepto del Gasto Fijo")
                monto_g = st.number_input("Importe (€)", min_value=0.0, step=0.50)
                metodo_g = st.selectbox("Pago", ["Tarjeta/PayPal", "Efectivo"])
                
                if st.form_submit_button("Guardar Gasto Fijo") and concepto_g and monto_g > 0:
                    conexion = sqlite3.connect(DB_PATH, timeout=10)
                    cursor = conexion.cursor()
                    try:
                        cursor.execute("INSERT INTO gastos_recurrentes (nombre_gasto, monto) VALUES (?, ?)", (concepto_g, monto_g))
                        cursor.execute("INSERT INTO movimientos_caja (fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago) VALUES (?, ?, ?, ?, ?)", 
                                       (datetime.now().strftime("%Y-%m-%d"), f"Fijo Automático: {concepto_g}", monto_g, "Gasto Habitual", metodo_g))
                        conexion.commit()
                        st.rerun()
                    except sqlite3.IntegrityError:
                        st.error("⚠️ Ya existe un gasto fijo recurrente con ese nombre exacto.")
                    finally:
                        conexion.close()

        else:
            with st.form("form_gasto_excepcional"):
                concepto_g = st.text_input("Concepto (Ej. Taller, Cena)")
                monto_g = st.number_input("Importe (€)", min_value=0.0, step=0.50)
                metodo_g = st.selectbox("Pago", ["Tarjeta/PayPal", "Efectivo"])
                
                if st.form_submit_button("Guardar Gasto Excepcional") and concepto_g and monto_g > 0:
                    registrar_movimiento(concepto_g, monto_g, "Gasto Excepcional", metodo_g)
                    st.rerun()

    with col_r:
        st.header("💰 Registrar Entrada de Dinero")
        with st.form("form_ingreso"):
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
    conexion = sqlite3.connect(DB_PATH, timeout=10)
    df_movimientos = pd.read_sql_query("SELECT * FROM movimientos_caja ORDER BY id DESC", conexion)
    conexion.close()
    
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
                    conexion = sqlite3.connect(DB_PATH, timeout=10)
                    conexion.execute("DELETE FROM movimientos_caja WHERE id = ?", (m_id,))
                    conexion.commit()
                    conexion.close()
                    st.rerun()
            st.markdown("<hr style='margin:0.2rem 0px;'/>", unsafe_allow_html=True)

elif opcion_menu == "🍏 Despensa (Alimentos)":
    st.title("🛒 Despensa de Alimentos")
    
    col1, col2 = st.columns(2)
    with col1: st.metric("🥘 Comida Consumida (Acumulado)", f"{coste_comida:,.2f} €")
    with col2: st.metric("🗑️ Mermas / Tirado", f"{mermas_comida:,.2f} €")
        
    with st.form("form_despensa_manual"):
        st.subheader("➕ Añadir stock manual (Sin registrar pago)")
        col_d1, col_d2, col_d3 = st.columns([4, 3, 3])
        with col_d1: nombre_d = st.text_input("Producto")
        with col_d2: super_d = st.selectbox("Origen", LISTA_SUPERS)
        with col_d3:
            unidades_d = st.number_input("Unidades", min_value=1, step=1)
            peso_d = st.number_input("Peso/L por ud.", min_value=0.01, value=1.0)
            precio_d = st.number_input("Precio/Ud aprox (€)", min_value=0.0, step=0.1)
            
        if st.form_submit_button("Añadir al Inventario") and nombre_d:
            conexion = sqlite3.connect(DB_PATH, timeout=10)
            conexion.execute("INSERT INTO despensa (producto_generico, supermercado, unidades_actuales, peso_neto_kg, precio_unitario, fecha_compra) VALUES (?, ?, ?, ?, ?, ?)",
                           (nombre_d.strip().lower(), super_d, unidades_d, peso_d, precio_d, datetime.now().strftime("%Y-%m-%d")))
            conexion.commit()
            conexion.close()
            st.rerun()

    st.markdown("---")
    st.header("📦 Existencias (Alimentos)")
    conexion = sqlite3.connect(DB_PATH, timeout=10)
    df_stock = pd.read_sql_query("SELECT * FROM despensa WHERE unidades_actuales > 0", conexion)
    conexion.close()
    
    if not df_stock.empty:
        df_stock['Precio_Kg_L'] = (df_stock['precio_unitario'] / df_stock['peso_neto_kg']).round(2)
        for index, fila in df_stock.iterrows():
            id_prod, nombre, superm, cant, peso, precio, p_kg = fila['id'], fila['producto_generico'].capitalize(), fila['supermercado'], fila['unidades_actuales'], fila['peso_neto_kg'], fila['precio_unitario'], fila['Precio_Kg_L']
            c_info, c_btn1, c_btn2 = st.columns([6, 2, 2])
            with c_info: st.write(f"🟢 **{nombre}** ({superm}) — **{cant} uds** | {peso} Kg/L | {precio}€/ud (**{p_kg} €/Kg**)")
            
            with c_btn1:
                if st.button(f"🍽️ Consumir 1 ud", key=f"con_{id_prod}"):
                    conexion = sqlite3.connect(DB_PATH, timeout=10)
                    conexion.execute("UPDATE despensa SET unidades_actuales = unidades_actuales - 1 WHERE id = ?", (id_prod,))
                    conexion.execute("INSERT INTO consumo_alimentos (fecha, producto_generico, cantidad, coste_estimado, estado) VALUES (?, ?, 1, ?, 'Consumido')", (datetime.now().strftime("%Y-%m-%d"), nombre.lower(), precio))
                    conexion.commit()
                    conexion.close()
                    
                    if cant - 1 == 0: 
                        mejor_super = obtener_mejor_super(nombre, tabla="despensa")
                        añadir_a_lista_compra(nombre.lower(), mejor_super)
                    st.rerun()
                    
            with c_btn2:
                if st.button(f"🗑️ Tirar / Merma", key=f"tir_{id_prod}"):
                    conexion = sqlite3.connect(DB_PATH, timeout=10)
                    conexion.execute("UPDATE despensa SET unidades_actuales = unidades_actuales - 1 WHERE id = ?", (id_prod,))
                    conexion.execute("INSERT INTO consumo_alimentos (fecha, producto_generico, cantidad, coste_estimado, estado) VALUES (?, ?, 1, ?, 'Tirado')", (datetime.now().strftime("%Y-%m-%d"), nombre.lower(), precio))
                    conexion.commit()
                    conexion.close()
                    
                    if cant - 1 == 0: 
                        mejor_super = obtener_mejor_super(nombre, tabla="despensa")
                        añadir_a_lista_compra(nombre.lower(), mejor_super)
                    st.rerun()
            st.markdown("<hr style='margin:0.2rem 0px;'/>", unsafe_allow_html=True)
    else: 
        st.info("No hay alimentos en la despensa.")

elif opcion_menu == "🏠 Utensilios (Hogar)":
    st.title("🏠 Inventario de Utensilios y Limpieza")
    
    st.metric(label="🧴 Valor Inmovilizado en Hogar", value=f"{inmovilizado_hogar:,.2f} €")
    
    with st.form("form_utensilios_manual"):
        st.subheader("➕ Añadir stock manual (Sin registrar pago)")
        col1, col2, col3 = st.columns([4, 3, 3])
        with col1: nombre_u = st.text_input("Producto")
        with col2: super_u = st.selectbox("Origen", LISTA_SUPERS)
        with col3:
            unidades_u = st.number_input("Unidades", min_value=1, step=1)
            precio_u = st.number_input("Precio/Ud aprox (€)", min_value=0.0, step=0.1)
            
        if st.form_submit_button("Añadir al Inventario") and nombre_u:
            conexion = sqlite3.connect(DB_PATH, timeout=10)
            conexion.execute("INSERT INTO utensilios (producto_generico, supermercado, unidades_actuales, peso_neto_kg, precio_unitario, fecha_compra) VALUES (?, ?, ?, 1.0, ?, ?)",
                           (nombre_u.strip().lower(), super_u, unidades_u, precio_u, datetime.now().strftime("%Y-%m-%d")))
            conexion.commit()
            conexion.close()
            st.rerun()
            
    st.markdown("---")
    st.header("📦 Existencias (Hogar)")
    conexion = sqlite3.connect(DB_PATH, timeout=10)
    utensilios = pd.read_sql_query("SELECT * FROM utensilios WHERE unidades_actuales > 0", conexion)
    conexion.close()
    
    if not utensilios.empty:
        for index, fila in utensilios.iterrows():
            id_prod = fila['id']
            nombre = fila['producto_generico'].capitalize()
            superm = fila['supermercado']
            cant = fila['unidades_actuales']
            precio = fila['precio_unitario']
            
            c1, c2, c3 = st.columns([6, 2, 2])
            with c1: st.write(f"🔹 **{nombre}** ({superm}) — **{cant} uds** | **{precio} €/ud**")
            with c2:
                if st.button("🧹 Gastar 1 ud", key=f"uso_hogar_{id_prod}"):
                    conexion = sqlite3.connect(DB_PATH, timeout=10)
                    conexion.execute("UPDATE utensilios SET unidades_actuales = unidades_actuales - 1 WHERE id = ?", (id_prod,))
                    conexion.commit()
                    conexion.close()
                    
                    if cant - 1 == 0: 
                        mejor_super = obtener_mejor_super(nombre, tabla="utensilios")
                        añadir_a_lista_compra(nombre.lower(), mejor_super)
                    st.rerun()
            with c3:
                if st.button("🗑️ Desechar", key=f"tir_hogar_{id_prod}"):
                    conexion = sqlite3.connect(DB_PATH, timeout=10)
                    conexion.execute("UPDATE utensilios SET unidades_actuales = unidades_actuales - 1 WHERE id = ?", (id_prod,))
                    conexion.commit()
                    conexion.close()
                    
                    if cant - 1 == 0: 
                        mejor_super = obtener_mejor_super(nombre, tabla="utensilios")
                        añadir_a_lista_compra(nombre.lower(), mejor_super)
                    st.rerun()
            st.markdown("<hr style='margin:0.2rem 0px;'/>", unsafe_allow_html=True)
    else: 
        st.info("No tienes utensilios o productos de limpieza registrados.")

elif opcion_menu == "🛒 Lista de la Compra":
    st.title("🛒 Lista de la Compra Inteligente")
    
    with st.form("form_lista_manual"):
        col1, col2, col3 = st.columns([5, 3, 2])
        with col1: prod_manual = st.text_input("Añadir producto suelto")
        with col2: super_manual = st.selectbox("Supermercado", LISTA_SUPERS)
        with col3: 
            st.write("")
            if st.form_submit_button("Añadir a la Lista") and prod_manual:
                añadir_a_lista_compra(prod_manual.capitalize(), super_manual)
                st.rerun()
                
    st.markdown("---")
    conexion = sqlite3.connect(DB_PATH, timeout=10)
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
                        conexion = sqlite3.connect(DB_PATH)
                        conexion.execute("DELETE FROM lista_compra WHERE id=?", (row['id'],))
                        conexion.commit()
                        conexion.close()
                        st.rerun()
                texto_export += f"[ ] {row['producto'].capitalize()}\n"
            texto_export += "\n"
            st.markdown("<hr style='margin:0.2rem 0px;'/>", unsafe_allow_html=True)
            
        st.markdown("<br>", unsafe_allow_html=True)
        
        def limpiar_bd_lista():
            conn_limpia = sqlite3.connect(DB_PATH)
            conn_limpia.execute("DELETE FROM lista_compra")
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
        with st.form("form_add_recurrente"):
            nombre_fijo = st.text_input("Nombre del Gasto").strip()
            monto_fijo = st.number_input("Importe Mensual (€)", min_value=0.1, step=5.0)
            if st.form_submit_button("Registrar") and nombre_fijo and monto_fijo > 0:
                try:
                    conexion = sqlite3.connect(DB_PATH, timeout=10)
                    conexion.execute("INSERT INTO gastos_recurrentes (nombre_gasto, monto) VALUES (?, ?)", (nombre_fijo, monto_fijo))
                    conexion.commit()
                    conexion.close()
                    st.rerun()
                except sqlite3.IntegrityError: 
                    st.error("⚠️ Ya existe ese gasto.")
                    
    with col_der:
        conexion = sqlite3.connect(DB_PATH, timeout=10)
        df_fijos = pd.read_sql_query("SELECT * FROM gastos_recurrentes", conexion)
        conexion.close()
        for index, fila in df_fijos.iterrows():
            c_txt, c_btn = st.columns([8, 2])
            with c_txt: st.write(f"💼 **{fila['nombre_gasto']}**: {fila['monto']:,.2f} € / mes")
            with c_btn:
                if st.button("🗑️ Eliminar", key=f"del_rec_{fila['id']}"):
                    conexion = sqlite3.connect(DB_PATH, timeout=10)
                    conexion.execute("DELETE FROM movimientos_caja WHERE concepto = ?", (f"Fijo Automático: {fila['nombre_gasto']}",))
                    conexion.execute("DELETE FROM gastos_recurrentes WHERE id = ?", (fila['id'],))
                    conexion.commit()
                    conexion.close()
                    st.rerun()

elif opcion_menu == "💳 Compras a Plazos":
    st.title("💳 Auditoría de Compras Financiadas a Plazos")
    col_l, col_r = st.columns([4, 6])
    
    with col_l:
        with st.form("form_plazos"):
            art_nombre = st.text_input("Artículo").strip()
            art_total = st.number_input("Coste Total (€)", min_value=1.0, step=50.0)
            art_meses = st.number_input("Meses", min_value=1, step=1, value=12)
            if st.form_submit_button("Auditar y Registrar"):
                cuota = art_total / art_meses
                if cuota < 25.0: 
                    st.error(f"🚨 Cuota de {cuota:.2f} €/mes. Regla rota. ¡A tocateja!")
                else:
                    try:
                        conexion = sqlite3.connect(DB_PATH, timeout=10)
                        conexion.execute("INSERT INTO compras_plazos (articulo, monto_total, meses_totales, meses_pagados) VALUES (?, ?, ?, 0)", (art_nombre, art_total, art_meses))
                        conexion.commit()
                        conexion.close()
                        st.rerun()
                    except: 
                        st.error("Ya existe.")
                        
    with col_r:
        conexion = sqlite3.connect(DB_PATH, timeout=10)
        df_plazos = pd.read_sql_query("SELECT * FROM compras_plazos", conexion)
        conexion.close()
        for index, fila in df_plazos.iterrows():
            cuota_actual = fila['monto_total'] / fila['meses_totales']
            st.write(f"💳 **{fila['articulo']}** | Cuota: **{cuota_actual:.2f} €/mes**")
            st.progress((fila['meses_pagados'] / fila['meses_totales']))
            if st.button("🗑️ Eliminar", key=f"del_plazo_{fila['id']}"):
                conexion = sqlite3.connect(DB_PATH, timeout=10)
                conexion.execute("DELETE FROM movimientos_caja WHERE concepto LIKE ?", (f"Cuota Plazo: {fila['articulo']} (%",))
                conexion.execute("DELETE FROM compras_plazos WHERE id = ?", (fila['id'],))
                conexion.commit()
                conexion.close()
                st.rerun()

# --- NUEVA PESTAÑA: PREVISIONES ANUALES ---
elif opcion_menu == "🗓️ Previsiones Anuales":
    st.title("🗓️ Gestor de Provisiones (Sinking Funds)")
    st.info("💡 Crea 'huchas virtuales' para pagos grandes (Seguro, IBI, Taller). El sistema dividirá el coste entre 12 y apartará esa cuota cada mes para que el pago no te pille por sorpresa.")
    
    col_p1, col_p2 = st.columns([4, 6])
    
    with col_p1:
        with st.form("form_nueva_prevision"):
            st.subheader("➕ Nueva Previsión")
            prev_concepto = st.text_input("Concepto (Ej. Seguro Coche, IBI)").strip()
            prev_monto = st.number_input("Coste Anual Estimado (€)", min_value=10.0, step=50.0)
            prev_mes = st.selectbox("Mes de Cobro", [1,2,3,4,5,6,7,8,9,10,11,12], format_func=lambda x: ["Enero","Febrero","Marzo","Abril","Mayo","Junio","Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"][x-1])
            
            if st.form_submit_button("Crear Previsión Anual") and prev_concepto:
                try:
                    conexion = sqlite3.connect(DB_PATH, timeout=10)
                    conexion.execute("INSERT INTO previsiones_anuales (concepto, monto_total, mes_objetivo) VALUES (?, ?, ?)", (prev_concepto, prev_monto, prev_mes))
                    conexion.commit()
                    conexion.close()
                    st.success("Regla de provisión creada.")
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.error("⚠️ Ya existe una previsión con ese nombre.")
                    
    with col_p2:
        st.subheader("🛡️ Tus Provisiones Activas")
        conexion = sqlite3.connect(DB_PATH, timeout=10)
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
                        conexion = sqlite3.connect(DB_PATH, timeout=10)
                        fecha_actual = datetime.now().strftime("%Y-%m-%d")
                        conexion.execute("INSERT INTO movimientos_caja (fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago) VALUES (?, ?, ?, 'Gasto Habitual', 'Tarjeta/PayPal')", 
                                         (fecha_actual, f"Pago Previsión: {fila['concepto']}", fila['monto_total']))
                        conexion.commit()
                        conexion.close()
                        st.success(f"Cobro de {fila['monto_total']}€ registrado en tu Banco.")
                        st.rerun()
                with c_del:
                    if st.button("❌", key=f"del_prev_{fila['id']}"):
                        conexion = sqlite3.connect(DB_PATH, timeout=10)
                        conexion.execute("DELETE FROM previsiones_anuales WHERE id = ?", (fila['id'],))
                        conexion.commit()
                        conexion.close()
                        st.rerun()
                st.markdown("<hr style='margin:0.2rem 0px;'/>", unsafe_allow_html=True)
        else:
            st.info("No tienes gastos anuales previstos. ¡Configura tu seguro o impuestos aquí!")

elif opcion_menu == "🔮 Previsiones y Proyectos":
    st.title("🔮 Consultor de Viabilidad")
    st.caption("Esta herramienta te dice cuánto dinero libre tienes realmente, tras apartar todo lo necesario para vivir, pagar tus deudas y tus seguros futuros.")
    
    col_p1, col_p2 = st.columns(2)
    with col_p1: sueldo_base = st.number_input("Nómina Fija Mensual (€)", min_value=0.0, value=1300.0)
    with col_p2: gastos_fijos_est = st.number_input("Suministros (Agua, Luz, Internet, etc)", min_value=0.0, value=150.0)
        
    # LA MATEMÁTICA DEFINITIVA DEL AHORRO LIBRE
    capacidad_ahorroador_teorica = sueldo_base - gastos_fijos_est - total_recurrentes - total_cuotas_plazos - total_provisiones_mes
    
    st.success(f"### 💰 AHORRO LIBRE REAL: {capacidad_ahorroador_teorica:,.2f} € / mes")
    
    st.markdown("**(Desglose Analítico):**")
    st.markdown(f"➕ Nómina: `{sueldo_base:,.2f} €`")
    st.markdown(f"➖ Suministros (Agua/Luz): `{gastos_fijos_est:,.2f} €`")
    st.markdown(f"➖ Recurrentes (Suscripciones/Letras): `{total_recurrentes:,.2f} €`")
    st.markdown(f"➖ Cuotas de Plazos: `{total_cuotas_plazos:,.2f} €`")
    st.markdown(f"➖ Provisiones (Colchón para Seguros/Taller): `{total_provisiones_mes:,.2f} €`")
    st.markdown("---")
    
    with st.form("form_proyecto"):
        st.subheader("🚀 Lanzar un Proyecto Finalista (Viajes, Caprichos)")
        c1, c2, c3 = st.columns(3)
        with c1: proj_name = st.text_input("Proyecto")
        with c2: proj_target = st.number_input("Objetivo (€)", min_value=10.0)
        with c3: proj_months = st.number_input("Meses", min_value=1, step=1)
        
        if st.form_submit_button("Lanzar Proyecto") and proj_name:
            cuota_necesaria = proj_target / proj_months if proj_months > 0 else proj_target
            if cuota_necesaria > capacidad_ahorroador_teorica:
                st.error(f"🚨 Inviable: La cuota necesaria ({cuota_necesaria:.2f} €) supera tu Ahorro Libre actual ({capacidad_ahorroador_teorica:.2f} €).")
            else:
                try:
                    conexion = sqlite3.connect(DB_PATH, timeout=10)
                    conexion.execute("INSERT INTO proyectos_futuros (nombre_proyecto, objetivo_total, meses_restantes) VALUES (?, ?, ?)", (proj_name, proj_target, proj_months))
                    conexion.commit()
                    conexion.close()
                    st.rerun()
                except sqlite3.IntegrityError: 
                    st.error("⚠️ Ya existe un proyecto con ese nombre.")
                
    conexion = sqlite3.connect(DB_PATH, timeout=10)
    df_proj = pd.read_sql_query("SELECT * FROM proyectos_futuros", conexion)
    conexion.close()
    
    for index, fila in df_proj.iterrows():
        faltan = fila['objetivo_total'] - fila['ahorrado_acumulado']
        cuota = faltan / fila['meses_restantes'] if fila['meses_restantes'] > 0 else faltan
        st.subheader(f"🎯 Proyecto: {fila['nombre_proyecto']}")
        st.progress(min(100.0, (fila['ahorrado_acumulado'] / fila['objetivo_total']) * 100) / 100.0)
        
        col_add1, col_add2, col_add3 = st.columns([2, 5, 3])
        with col_add1: abono = st.number_input(f"Abonar (€)", min_value=0.0, step=10.0, key=f"num_{fila['id']}")
        with col_add2:
            st.write("")
            if st.button("Confirmar", key=f"btn_h_{fila['id']}") and abono > 0:
                conexion = sqlite3.connect(DB_PATH, timeout=10)
                conexion.execute("UPDATE proyectos_futuros SET ahorrado_acumulado = ahorrado_acumulado + ? WHERE id = ?", (abono, fila['id']))
                conexion.execute("INSERT INTO movimientos_caja (fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago) VALUES (?, ?, ?, 'Gasto Habitual', 'Tarjeta/PayPal')", 
                                 (datetime.now().strftime("%Y-%m-%d"), f"Abono hucha: {fila['nombre_proyecto']}", abono))
                conexion.commit()
                conexion.close()
                st.rerun()
        with col_add3:
            st.write("")
            if st.button("🗑️ Eliminar Proyecto", key=f"del_proj_{fila['id']}"):
                conexion = sqlite3.connect(DB_PATH, timeout=10)
                conexion.execute("DELETE FROM movimientos_caja WHERE concepto = ?", (f"Abono hucha: {fila['nombre_proyecto']}",))
                conexion.execute("DELETE FROM proyectos_futuros WHERE id = ?", (fila['id'],))
                conexion.commit()
                conexion.close()
                st.rerun()
        st.markdown("<hr style='margin:0.5rem 0px;'/>", unsafe_allow_html=True)

elif opcion_menu == "🚗 Mi Coche":
    st.title("🚗 Dashboard del Vehículo")
    
    # Lectura de la configuración fija del coche (sin incluir el seguro, que ahora va en Previsiones)
    conexion = sqlite3.connect(DB_PATH, timeout=10)
    config_coche = pd.read_sql_query("SELECT letra_mensual FROM configuracion_coche WHERE id=1", conexion)
    letra_val = config_coche.iloc[0]['letra_mensual'] if not config_coche.empty else 0.0
    
    mes_actual = datetime.now().strftime("%Y-%m")
    variables_df = pd.read_sql_query("SELECT * FROM movimientos_caja WHERE tipo_ingreso_gasto = 'Gasto Coche' AND fecha LIKE ?", conexion, params=(f"{mes_actual}%",))
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
        with st.form("form_coche_fijos"):
            st.caption("Ajusta tu letra mensual para el cálculo estadístico.")
            nueva_letra = st.number_input("Letra del Coche (€/mes)", min_value=0.0, step=10.0, value=float(letra_val))
            
            if st.form_submit_button("Guardar Letra"):
                conexion = sqlite3.connect(DB_PATH, timeout=10)
                conexion.execute("UPDATE configuracion_coche SET letra_mensual=? WHERE id=1", (nueva_letra,))
                conexion.commit()
                conexion.close()
                st.success("Letra del coche actualizada.")
                st.rerun()
                
    with col_der:
        st.header("⛽ Añadir Gasto Variable")
        with st.form("form_coche_var"):
            st.caption("Estos gastos se restarán inmediatamente de tu Banco o Efectivo.")
            tipo_gasto_coche = st.selectbox("Categoría", ["Gasolina", "Lavadero / Limpieza", "Taller / Mantenimiento", "Peaje / Parking", "Otro"])
            monto_coche = st.number_input("Importe (€)", min_value=0.0, step=5.0)
            metodo_coche = st.selectbox("Pago", ["Tarjeta/PayPal", "Efectivo"])
            
            if st.form_submit_button("Registrar Gasto") and monto_coche > 0:
                fecha_actual = datetime.now().strftime("%Y-%m-%d")
                conexion = sqlite3.connect(DB_PATH, timeout=10)
                conexion.execute("INSERT INTO movimientos_caja (fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago) VALUES (?, ?, ?, 'Gasto Coche', ?)",
                               (fecha_actual, f"Coche: {tipo_gasto_coche}", monto_coche, metodo_coche))
                conexion.commit()
                conexion.close()
                st.rerun()
                
    st.markdown("---")
    st.subheader("📜 Historial de Variables (Este Mes)")
    if not variables_df.empty:
        for _, row in variables_df.iloc[::-1].iterrows():
            c1, c2 = st.columns([9, 1])
            with c1: st.write(f"📅 **{row['fecha']}** | {row['concepto']} -> **{row['monto']:,.2f} €** ({row['metodo_pago']})")
            with c2:
                if st.button("❌", key=f"del_coche_{row['id']}"):
                    conexion = sqlite3.connect(DB_PATH, timeout=10)
                    conexion.execute("DELETE FROM movimientos_caja WHERE id=?", (row['id'],))
                    conexion.commit()
                    conexion.close()
                    st.rerun()
            st.markdown("<hr style='margin:0.2rem 0px;'/>", unsafe_allow_html=True)
    else:
        st.info("No has registrado gasolina ni limpiezas este mes.")

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
                with st.spinner("Procesando con IA..."):
                    try:
                        client = genai.Client(api_key=api_key)
                        imagen.save("temp_ticket.png")
                        prompt = "Analiza este ticket y devuelve estrictamente un objeto JSON con las claves: supermercado, metodo_pago, articulos_despensa (lista de objetos: producto, unidades, peso_kg, precio_unitario), y gastos_hogar (lista: concepto, precio_total). Sin explicaciones, solo el JSON."
                        
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
            
            c_l, c_r = st.columns(2)
            with c_l:
                df_desp = pd.DataFrame(datos.get('articulos_despensa', []))
                if not df_desp.empty: st.dataframe(df_desp, use_container_width=True)
            with c_r:
                df_hogar = pd.DataFrame(datos.get('gastos_hogar', []))
                if not df_hogar.empty: st.dataframe(df_hogar, use_container_width=True)
                
            if st.button("🔨 Inyectar Todo al Sistema"):
                conexion = sqlite3.connect(DB_PATH, timeout=10)
                cursor = conexion.cursor()
                fecha_actual = datetime.now().strftime("%Y-%m-%d")
                
                for item in datos.get('articulos_despensa', []):
                    producto = item.get('producto', 'Alimento sin clasificar').lower().strip()
                    unidades = item.get('unidades', 1)
                    peso = item.get('peso_kg', 1.0)
                    precio = item.get('precio_unitario', 0.0)
                    
                    cursor.execute("INSERT INTO despensa (producto_generico, supermercado, unidades_actuales, peso_neto_kg, precio_unitario, fecha_compra) VALUES (?, ?, ?, ?, ?, ?)", 
                                   (producto, super_det, unidades, peso, precio, fecha_actual))
                    cursor.execute("INSERT INTO movimientos_caja (fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago) VALUES (?, ?, ?, 'Gasto Habitual', ?)", 
                                   (fecha_actual, f"Alimento: {producto}", unidades * precio, pago_det))
                
                for gasto in datos.get('gastos_hogar', []):
                    concepto_hogar = gasto.get('concepto', 'Utensilio desconocido').capitalize()
                    precio_total_hogar = gasto.get('precio_total', 0.0)
                    
                    cursor.execute("INSERT INTO utensilios (producto_generico, supermercado, unidades_actuales, peso_neto_kg, precio_unitario, fecha_compra) VALUES (?, ?, 1, 1.0, ?, ?)", 
                                   (concepto_hogar.lower(), super_det, precio_total_hogar, fecha_actual))
                    cursor.execute("INSERT INTO movimientos_caja (fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago) VALUES (?, ?, ?, 'Gasto Habitual', ?)", 
                                   (fecha_actual, f"Bazar/Utensilio: {concepto_hogar}", precio_total_hogar, pago_det))
                
                conexion.commit()
                conexion.close()
                del st.session_state['resultado_json_ticket']
                st.rerun()

elif opcion_menu == "⚙️ Configuración y Arranque":
    st.title("⚙️ Carga de Saldos Iniciales (Onboarding)")
    st.info("💡 Usa esta pestaña solo para configurar tu punto de partida. Carga lo que tienes en casa hoy. Una vez termines de volcar tu casa, puedes ignorar o borrar esta pestaña.")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.header("💰 1. Dinero Inicial")
        with st.form("form_saldos_iniciales"):
            st.caption("Esto inyectará dinero en el sistema de forma 'fantasma' (no aparecerá en el historial de gastos/ingresos para mantenerlo limpio).")
            saldo_banco_ini = st.number_input("Dinero real en tu Banco (€)", min_value=0.0, step=100.0)
            saldo_hucha_ini = st.number_input("Dinero real en tu Cartera/Hucha (€)", min_value=0.0, step=50.0)
            
            if st.form_submit_button("Cargar Dinero al Sistema"):
                conexion = sqlite3.connect(DB_PATH, timeout=10)
                fecha_actual = datetime.now().strftime("%Y-%m-%d")
                
                if saldo_banco_ini > 0:
                    conexion.execute("INSERT INTO movimientos_caja (fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago, subcuenta_extra) VALUES (?, 'Saldo Inicial: Banco', ?, 'Ingreso Extra', 'Tarjeta/PayPal', 'Extra-Banco')", 
                                     (fecha_actual, saldo_banco_ini))
                if saldo_hucha_ini > 0:
                    conexion.execute("INSERT INTO movimientos_caja (fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago, subcuenta_extra) VALUES (?, 'Saldo Inicial: Efectivo', ?, 'Ingreso Extra', 'Efectivo', 'Extra-Efectivo')", 
                                     (fecha_actual, saldo_hucha_ini))
                
                conexion.commit()
                conexion.close()
                st.success("Saldos cargados. ¡Ve a Control de Caja y verás la magia!")
                st.rerun()

    with col2:
        st.header("📦 2. Inventario Rápido")
        with st.form("form_inv_rapido"):
            st.caption("Carga artículos a coste 0.00 € y Origen 'Stock Inicial' para no alterar tus estadísticas financieras de consumo de este mes.")
            
            tipo_inv = st.radio("¿Dónde lo guardamos?", ["Despensa", "Hogar"], horizontal=True)
            nombre_inv = st.text_input("Nombre del Producto (Ej. Leche, Papel Higiénico)")
            unidades_inv = st.number_input("Cantidad", min_value=1, step=1)
            
            if st.form_submit_button("Cargar a Inventario") and nombre_inv:
                tabla = "despensa" if tipo_inv == "Despensa" else "utensilios"
                conexion = sqlite3.connect(DB_PATH, timeout=10)
                
                conexion.execute(f"INSERT INTO {tabla} (producto_generico, supermercado, unidades_actuales, peso_neto_kg, precio_unitario, fecha_compra) VALUES (?, 'Stock Inicial', ?, 1.0, 0.0, ?)",
                               (nombre_inv.strip().lower(), unidades_inv, datetime.now().strftime("%Y-%m-%d")))
                
                conexion.commit()
                conexion.close()
                st.success(f"{unidades_inv}x {nombre_inv.capitalize()} añadidos a tu {tipo_inv}.")
                st.rerun()
