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
    try:
        cursor.execute("ALTER TABLE despensa ADD COLUMN fecha_compra TEXT")
    except:
        pass

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
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS utensilios (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL
    )""")
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS lista_compra (
        id INTEGER PRIMARY KEY AUTOINCREMENT, producto TEXT NOT NULL, supermercado_recomendado TEXT NOT NULL
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
    cursor = conexion.cursor()
    cursor.execute("SELECT monto_total, meses_totales FROM compras_plazos WHERE meses_pagados < meses_totales")
    total_cuotas_plazos = sum(row[0] / row[1] for row in cursor.fetchall())
    conexion.close()
    saldo_b = ingresos_fijos + extras_banco - gastos_tarjeta
    saldo_e = extras_efectivo - gastos_efectivo
    return saldo_b, saldo_e, extras_banco, excepcionales, coste_comida, mermas_comida, total_recurrentes, total_cuotas_plazos

saldo_banco, saldo_efectivo, bizums_bloqueados, excepcionales, coste_comida, mermas_comida, total_recurrentes, total_cuotas_plazos = obtener_totales_sistema()

def registrar_movimiento(concepto, monto, tipo, metodo, subcuenta='N/A'):
    conexion = sqlite3.connect(DB_PATH, timeout=10)
    cursor = conexion.cursor()
    cursor.execute("INSERT INTO movimientos_caja (fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago, subcuenta_extra) VALUES (?, ?, ?, ?, ?, ?)", 
                   (datetime.now().strftime("%Y-%m-%d"), concepto, monto, tipo, metodo, subcuenta))
    conexion.commit()
    conexion.close()

def obtener_mejor_super(producto_nombre):
    conexion = sqlite3.connect(DB_PATH, timeout=10)
    fecha_limite = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
    q = "SELECT supermercado FROM despensa WHERE producto_generico = ? AND fecha_compra >= ? GROUP BY supermercado ORDER BY AVG(precio_unitario/peso_neto_kg) ASC LIMIT 1"
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
    "💵 Control de Caja", "🍏 Despensa (Alimentos)", "🏠 Utensilios (Hogar)",
    "🛒 Lista de la Compra", "🔄 Gastos Recurrentes", "💳 Compras a Plazos", 
    "🔮 Previsiones y Proyectos", "📷 Lector de Tickets IA"
])

# ==========================================
# VISTAS DE LA APLICACIÓN
# ==========================================
if opcion_menu == "💵 Control de Caja":
    st.title("🧠 Tu Copiloto Financiero Inteligente")
    st.markdown("---")
    col1, col2, col3, col4 = st.columns(4)
    with col1: st.metric(label="💳 Bolsa Única (Banco)", value=f"{saldo_banco:,.2f} €")
    with col2: st.metric(label="💵 Hucha Efectivo (Físico)", value=f"{saldo_efectivo:,.2f} €")
    with col3: st.metric(label="🔒 Extras en Banco (Bloqueado)", value=f"{bizums_bloqueados:,.2f} €")
    with col4: st.metric(label="🚨 Gastos Excepcionales", value=f"{excepcionales:,.2f} €")
    st.markdown("---")
    col_l, col_r = st.columns(2)
    with col_l:
        st.header("🛒 Registrar Gasto Manual")
        with st.form("form_gasto"):
            concepto_g = st.text_input("Concepto")
            monto_g = st.number_input("Importe (€)", min_value=0.0, step=0.50)
            tipo_g = st.selectbox("Tipo", ["Gasto Habitual", "Gasto Excepcional"])
            metodo_g = st.selectbox("Pago", ["Tarjeta/PayPal", "Efectivo"])
            if st.form_submit_button("Guardar Gasto") and concepto_g and monto_g > 0:
                registrar_movimiento(concepto_g, monto_g, tipo_g, metodo_g)
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
            elif tipo.startswith('Gasto') or tipo in ['Gasto Habitual', 'Gasto Excepcional']:
                if pago == 'Tarjeta/PayPal': running_banco -= monto
                elif pago == 'Efectivo': running_hucha -= monto
            saldos_registro[m_id] = (running_banco, running_hucha)
        for index, fila in df_movimientos.iterrows():
            m_id, m_fecha, m_concepto, m_monto, m_tipo, m_pago, m_sub = fila['id'], fila['fecha'], fila['concepto'], fila['monto'], fila['tipo_ingreso_gasto'], fila['metodo_pago'], fila['subcuenta_extra']
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
                    if cant - 1 == 0: añadir_a_lista_compra(nombre.lower(), obtener_mejor_super(nombre))
                    conexion.commit()
                    conexion.close()
                    st.rerun()
            with c_btn2:
                if st.button(f"🗑️ Tirar / Merma", key=f"tir_{id_prod}"):
                    conexion = sqlite3.connect(DB_PATH, timeout=10)
                    conexion.execute("UPDATE despensa SET unidades_actuales = unidades_actuales - 1 WHERE id = ?", (id_prod,))
                    conexion.execute("INSERT INTO consumo_alimentos (fecha, producto_generico, cantidad, coste_estimado, estado) VALUES (?, ?, 1, ?, 'Tirado')", (datetime.now().strftime("%Y-%m-%d"), nombre.lower(), precio))
                    if cant - 1 == 0: añadir_a_lista_compra(nombre.lower(), obtener_mejor_super(nombre))
                    conexion.commit()
                    conexion.close()
                    st.rerun()
            st.markdown("<hr style='margin:0.2rem 0px;'/>", unsafe_allow_html=True)
    else:
        st.info("No hay alimentos en la despensa.")

elif opcion_menu == "🏠 Utensilios (Hogar)":
    st.title("🏠 Inventario de Utensilios de Hogar")
    with st.form("form_utensilios"):
        col1, col2 = st.columns([8, 2])
        with col1: nombre_u = st.text_input("Nombre del Utensilio")
        with col2: 
            st.write("")
            submit_u = st.form_submit_button("Añadir")
        if submit_u and nombre_u:
            conexion = sqlite3.connect(DB_PATH, timeout=10)
            conexion.execute("INSERT INTO utensilios (nombre) VALUES (?)", (nombre_u.strip().capitalize(),))
            conexion.commit()
            conexion.close()
            st.rerun()
    st.markdown("---")
    conexion = sqlite3.connect(DB_PATH, timeout=10)
    utensilios = pd.read_sql_query("SELECT * FROM utensilios", conexion)
    conexion.close()
    if not utensilios.empty:
        for index, row in utensilios.iterrows():
            c1, c2, c3 = st.columns([6, 2, 2])
            with c1: st.write(f"🔹 **{row['nombre']}**")
            with c2:
                if st.button("🛒 Gastado (A Lista)", key=f"ut_lista_{row['id']}"):
                    añadir_a_lista_compra(row['nombre'], "Sección Bazar/Hogar")
                    conexion = sqlite3.connect(DB_PATH, timeout=10)
                    conexion.execute("DELETE FROM utensilios WHERE id = ?", (row['id'],))
                    conexion.commit()
                    conexion.close()
                    st.rerun()
            with c3:
                if st.button("🗑️ Borrar", key=f"ut_del_{row['id']}"):
                    conexion = sqlite3.connect(DB_PATH, timeout=10)
                    conexion.execute("DELETE FROM utensilios WHERE id = ?", (row['id'],))
                    conexion.commit()
                    conexion.close()
                    st.rerun()
            st.markdown("<hr style='margin:0.2rem 0px;'/>", unsafe_allow_html=True)
    else:
        st.info("No tienes utensilios registrados.")

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
       
