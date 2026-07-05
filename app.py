import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime
from PIL import Image
import json
import os
from google import genai

# --- BLOQUE DE SEGURIDAD (AÑADIR AL PRINCIPIO) ---
def check_password():
    """Devuelve True si la contraseña es correcta."""
    def password_entered():
        if st.session_state["password"] == st.secrets["APP_PASSWORD"]:
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
    st.stop()  # Detiene la carga de la app si no hay clave correcta
# --------------------------------------------------

# CONFIGURACIÓN DE LA PÁGINA
st.set_page_config(page_title="Copiloto Financiero Doméstico", layout="wide")

DB_PATH = "economia_casa.db"

# ==========================================
# INICIALIZACIÓN DE LA BASE DE DATOS
# ==========================================
def inicializar_base_datos():
    conexion = sqlite3.connect(DB_PATH, timeout=10)
    cursor = conexion.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS movimientos_caja (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT NOT NULL,
        concepto TEXT NOT NULL,
        monto REAL NOT NULL,
        tipo_ingreso_gasto TEXT CHECK(tipo_ingreso_gasto IN (
            'Ingreso Fijo', 'Ingreso Extra', 'Gasto Habitual', 'Gasto Excepcional'
        )),
        metodo_pago TEXT CHECK(metodo_pago IN ('Tarjeta/PayPal', 'Efectivo')),
        subcuenta_extra TEXT CHECK(subcuenta_extra IN (
            'N/A', 'Extra-Efectivo', 'Extra-Banco'
        )) DEFAULT 'N/A'
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS despensa (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        producto_generico TEXT NOT NULL,
        supermercado TEXT NOT NULL,
        unidades_actuales INTEGER NOT NULL DEFAULT 0,
        peso_neto_kg REAL NOT NULL,
        precio_unitario REAL NOT NULL
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS consumo_alimentos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT NOT NULL,
        producto_generico TEXT NOT NULL,
        cantidad INTEGER NOT NULL,
        coste_estimado REAL NOT NULL,
        estado TEXT CHECK(estado IN ('Consumido', 'Tirado'))
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS proyectos_futuros (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre_proyecto TEXT NOT NULL UNIQUE,
        objetivo_total REAL NOT NULL,
        meses_restantes INTEGER NOT NULL,
        ahorrado_acumulado REAL NOT NULL DEFAULT 0.0
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS gastos_recurrentes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre_gasto TEXT NOT NULL UNIQUE,
        monto REAL NOT NULL
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS compras_plazos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        articulo TEXT NOT NULL UNIQUE,
        monto_total REAL NOT NULL,
        meses_totales INTEGER NOT NULL,
        meses_pagados INTEGER NOT NULL DEFAULT 0
    )
    """)
    
    conexion.commit()
    conexion.close()

# ==========================================
# PROCESAMIENTO MENSUAL AUTOMÁTICO
# ==========================================
def ejecutar_automatizaciones_mensuales():
    mes_actual = datetime.now().strftime("%Y-%m")
    conexion = sqlite3.connect(DB_PATH, timeout=10)
    cursor = conexion.cursor()
    
    # A. Cobros Automáticos de Gastos Recurrentes
    cursor.execute("SELECT nombre_gasto, monto FROM gastos_recurrentes")
    for nombre, monto in cursor.fetchall():
        concepto_cargo = f"Fijo Automático: {nombre}"
        q_check = "SELECT COUNT(*) FROM movimientos_caja WHERE concepto = ? AND fecha LIKE ?"
        cursor.execute(q_check, (concepto_cargo, f"{mes_actual}%"))
        if cursor.fetchone()[0] == 0:
            q_ins = (
                "INSERT INTO movimientos_caja "
                "(fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago) "
                "VALUES (?, ?, ?, 'Gasto Habitual', 'Tarjeta/PayPal')"
            )
            cursor.execute(q_ins, (datetime.now().strftime("%Y-%m-%d"), concepto_cargo, monto))
            
    # B. Cobros Automáticos de Cuotas de Compras a Plazos
    q_plazos = "SELECT id, articulo, monto_total, meses_totales, meses_pagados FROM compras_plazos WHERE meses_pagados < meses_totales"
    cursor.execute(q_plazos)
    for pid, articulo, total, m_totales, m_pagados in cursor.fetchall():
        concepto_base = f"Cuota Plazo: {articulo} (%"
        q_check_p = "SELECT COUNT(*) FROM movimientos_caja WHERE concepto LIKE ? AND fecha LIKE ?"
        cursor.execute(q_check_p, (concepto_base, f"{mes_actual}%"))
        
        if cursor.fetchone()[0] == 0:
            concepto_cuota = f"Cuota Plazo: {articulo} ({m_pagados + 1}/{m_totales})"
            cuota_mensual = total / m_totales
            q_ins_p = (
                "INSERT INTO movimientos_caja "
                "(fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago) "
                "VALUES (?, ?, ?, 'Gasto Habitual', 'Tarjeta/PayPal')"
            )
            cursor.execute(q_ins_p, (datetime.now().strftime("%Y-%m-%d"), concepto_cuota, cuota_mensual))
            cursor.execute("UPDATE compras_plazos SET meses_pagados = meses_pagados + 1 WHERE id = ?", (pid,))
            
    conexion.commit()
    conexion.close()

inicializar_base_datos()
ejecutar_automatizaciones_mensuales()

# ==========================================
# CÁLCULOS Y AGREGADOS DE BALANCES REALES
# ==========================================
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
    
    saldo_banco = ingresos_fijos + extras_banco - gastos_tarjeta
    saldo_efectivo = extras_efectivo - gastos_efectivo
    
    return saldo_banco, saldo_efectivo, extras_banco, excepcionales, coste_comida, mermas_comida, total_recurrentes, total_cuotas_plazos

saldo_banco, saldo_efectivo, bizums_bloqueados, excepcionales, coste_comida, mermas_comida, total_recurrentes, total_cuotas_plazos = obtener_totales_sistema()

LISTA_SUPERS = ["Mercadona", "Lidl", "Carrefour", "Aldi", "Family Cash", "Dia", "Otros"]

def registrar_movimiento(concepto, monto, tipo, metodo, subcuenta='N/A'):
    conexion = sqlite3.connect(DB_PATH, timeout=10)
    cursor = conexion.cursor()
    fecha_actual = datetime.now().strftime("%Y-%m-%d")
    q_mov = (
        "INSERT INTO movimientos_caja "
        "(fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago, subcuenta_extra) "
        "VALUES (?, ?, ?, ?, ?, ?)"
    )
    cursor.execute(q_mov, (fecha_actual, concepto, monto, tipo, metodo, subcuenta))
    conexion.commit()
    conexion.close()

# --- SIDEBAR NAVEGACIÓN ---
st.sidebar.title("📌 Configuración General")
api_key_input = st.sidebar.text_input("Introduce tu Gemini API Key:", type="password")
st.sidebar.markdown("---")
opcion_menu = st.sidebar.radio("Ir a:", [
    "💵 Control de Caja", 
    "🍏 Despensa e Inventario", 
    "🔄 Gastos Recurrentes", 
    "💳 Compras a Plazos", 
    "🔮 Previsiones y Proyectos", 
    "📷 Lector de Tickets IA"
])

# ==========================================
# VISTA 1: CONTROL DE CAJA
# ==========================================
if opcion_menu == "💵 Control de Caja":
    st.title("🧠 Tu Copiloto Financiero Inteligente")
    st.subheader("Control analítico de flujo de caja y presupuestos")
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
    st.header("📜 Historial de Movimientos con Balance Cuenta")
    conexion = sqlite3.connect(DB_PATH, timeout=10)
    q_hist = (
        "SELECT id, fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago, subcuenta_extra "
        "FROM movimientos_caja ORDER BY id DESC"
    )
    df_movimientos = pd.read_sql_query(q_hist, conexion)
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
            
            es_hucha = (m_pago == 'Efectivo' or m_sub == 'Extra-Efectivo')
            txt_balance = f"💵 Balance Hucha: **{bal_hucha:,.2f} €**" if es_hucha else f"🏦 Balance Banco: **{bal_banco:,.2f} €**"
            
            c_detalles, c_eliminar = st.columns([8, 2])
            with c_detalles: 
                st.write(f"📅 **{m_fecha}** | `{m_tipo}` | **{m_concepto}** -> **{m_monto:,.2f} €** ({m_pago}) | {txt_balance}")
            with c_eliminar:
                if st.button("🗑️ Borrar", key=f"del_mov_{m_id}"):
                    conexion = sqlite3.connect(DB_PATH, timeout=10); cursor = conexion.cursor()
                    cursor.execute("DELETE FROM movimientos_caja WHERE id = ?", (m_id,))
                    conexion.commit(); conexion.close(); st.rerun()
            st.markdown("<hr style='margin:0.2rem 0px;'/>", unsafe_allow_html=True)

# ==========================================
# VISTA 2: DESPENSA E INVENTARIO
# ==========================================
elif opcion_menu == "🍏 Despensa e Inventario":
    st.title("🛒 Despensa Virtual e Inventario Inteligente")
    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1: st.metric(label="🥘 Gasto Acumulado en Comida Consumida", value=f"{coste_comida:,.2f} €")
    with col2: st.metric(label="🗑️ Dinero Tirado a la Basura (Mermas)", value=f"{mermas_comida:,.2f} €", delta="Incluido en el gasto total", delta_color="inverse")
    st.markdown("---")
    st.header("➕ Añadir Alimento a la Despensa")
    with st.form("form_despensa"):
        col_f1, col_f2, col_f3 = st.columns(3)
        with col_f1:
            prod_nom = st.text_input("Producto Genérico").lower().strip()
            super_nom = st.selectbox("Supermercado", LISTA_SUPERS)
        with col_f2:
            prod_uni = st.number_input("Unidades compradas", min_value=1, step=1)
            prod_peso = st.number_input("Peso Neto por Unidad (Kg o Litros)", min_value=0.01, step=0.05, format="%.3f")
        with col_f3:
            prod_prec = st.number_input("Precio Unitario (€)", min_value=0.01, step=0.10)
            pago_despensa = st.selectbox("Método de Pago de la Compra", ["Tarjeta/PayPal", "Efectivo"])
        if st.form_submit_button("Ingresar en Despensa") and prod_nom and prod_uni > 0:
            conexion = sqlite3.connect(DB_PATH, timeout=10); cursor = conexion.cursor()
            q_add_d = (
                "INSERT INTO despensa "
                "(producto_generico, supermercado, unidades_actuales, peso_neto_kg, precio_unitario) "
                "VALUES (?, ?, ?, ?, ?)"
            )
            cursor.execute(q_add_d, (prod_nom, super_nom, prod_uni, prod_peso, prod_prec))
            coste_total_compra = prod_uni * prod_prec
            fecha_act = datetime.now().strftime("%Y-%m-%d")
            q_add_c = (
                "INSERT INTO movimientos_caja "
                "(fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago) "
                "VALUES (?, ?, ?, 'Gasto Habitual', ?)"
            )
            cursor.execute(q_add_c, (fecha_act, f"Compra {super_nom}: {prod_nom} x{prod_uni}", coste_total_compra, pago_despensa))
            conexion.commit(); conexion.close(); st.rerun()
    st.markdown("---")
    st.header("📦 Existencias en Despensa")
    conexion = sqlite3.connect(DB_PATH, timeout=10)
    df_stock = pd.read_sql_query("SELECT id, producto_generico, supermercado, unidades_actuales, peso_neto_kg, precio_unitario FROM despensa WHERE unidades_actuales > 0", conexion)
    conexion.close()
    if not df_stock.empty:
        df_stock['Precio por Kg/L'] = (df_stock['precio_unitario'] / df_stock['peso_neto_kg']).round(2)
        for index, fila in df_stock.iterrows():
            id_prod, nombre, superm, cant, peso, precio, p_kg = fila['id'], fila['producto_generico'].capitalize(), fila['supermercado'], fila['unidades_actuales'], fila['peso_neto_kg'], fila['precio_unitario'], fila['Precio por Kg/L']
            c_info, c_btn1, c_btn2 = st.columns([6, 2, 2])
            with c_info: st.write(f"🟢 **{nombre}** ({superm}) — **{cant} uds** restantes | Tamaño: {peso} Kg/L | Coste: {precio}€/ud (**{p_kg} €/Kg**)")
            with c_btn1:
                if st.button(f"🍽️ Consumir 1 ud", key=f"con_{id_prod}"):
                    conexion = sqlite3.connect(DB_PATH, timeout=10); cursor = conexion.cursor()
                    cursor.execute("UPDATE despensa SET unidades_actuales = unidades_actuales - 1 WHERE id = ?", (id_prod,))
                    q_cons = (
                        "INSERT INTO consumo_alimentos "
                        "(fecha, producto_generico, cantidad, coste_estimado, estado) "
                        "VALUES (?, ?, 1, ?, 'Consumido')"
                    )
                    cursor.execute(q_cons, (datetime.now().strftime("%Y-%m-%d"), nombre.lower(), precio))
                    conexion.commit(); conexion.close(); st.rerun()
            with c_btn2:
                if st.button(f"🗑️ Tirar / Merma", key=f"tir_{id_prod}"):
                    conexion = sqlite3.connect(DB_PATH, timeout=10); cursor = conexion.cursor()
                    cursor.execute("UPDATE despensa SET unidades_actuales = unidades_actuales - 1 WHERE id = ?", (id_prod,))
                    q_merm = (
                        "INSERT INTO consumo_alimentos "
                        "(fecha, producto_generico, cantidad, coste_estimado, estado) "
                        "VALUES (?, ?, 1, ?, 'Tirado')"
                    )
                    cursor.execute(q_merm, (datetime.now().strftime("%Y-%m-%d"), nombre.lower(), precio))
                    conexion.commit(); conexion.close(); st.rerun()
            st.markdown("<hr style='margin:0.2rem 0px;'/>", unsafe_allow_html=True)
            
    st.markdown("---")
    st.header("🔍 Comparador de Eficiencia")
    conexion = sqlite3.connect(DB_PATH, timeout=10)
    df_todos = pd.read_sql_query("SELECT producto_generico, supermercado, peso_neto_kg, precio_unitario FROM despensa", conexion)
    conexion.close()
    if not df_todos.empty:
        df_todos['precio_kg'] = (df_todos['precio_unitario'] / df_todos['peso_neto_kg']).round(2)
        productos_unicos = df_todos['producto_generico'].unique()
        busqueda = st.selectbox("Selecciona un producto:", [p.capitalize() for p in productos_unicos])
        if busqueda:
            df_filtrado = df_todos[df_todos['producto_generico'] == busqueda.lower()]
            df_comparativo = df_filtrado.groupby('supermercado')['precio_kg'].min().reset_index()
            st.table(df_comparativo.rename(columns={'supermercado': 'Supermercado', 'precio_kg': 'Mejor Precio registrado (€/Kg o L)'}))

# ==========================================
# VISTA 3: GASTOS RECURRENTES (CON BORRADO EN CASCADA)
# ==========================================
elif opcion_menu == "🔄 Gastos Recurrentes":
    st.title("🔄 Gestión de Gastos Fijos y Recurrentes")
    st.markdown("---")
    col_izq, col_der = st.columns([4, 6])
    with col_izq:
        st.header("➕ Añadir Gasto Fijo")
        with st.form("form_add_recurrente"):
            nombre_fijo = st.text_input("Nombre del Gasto").strip()
            monto_fijo = st.number_input("Importe Mensual (€)", min_value=0.1, step=5.0)
            if st.form_submit_button("Registrar Gasto Recurrente") and nombre_fijo and monto_fijo > 0:
                try:
                    conexion = sqlite3.connect(DB_PATH, timeout=10); cursor = conexion.cursor()
                    cursor.execute("INSERT INTO gastos_recurrentes (nombre_gasto, monto) VALUES (?, ?)", (nombre_fijo, monto_fijo))
                    conexion.commit(); conexion.close(); st.rerun()
                except sqlite3.IntegrityError: st.error("⚠️ Ya existe ese gasto.")
    with col_der:
        st.header("📋 Recibos Fijos Activos")
        conexion = sqlite3.connect(DB_PATH, timeout=10)
        df_fijos = pd.read_sql_query("SELECT id, nombre_gasto, monto FROM gastos_recurrentes", conexion)
        conexion.close()
        if not df_fijos.empty:
            for index, fila in df_fijos.iterrows():
                f_id, f_nombre, f_monto = fila['id'], fila['nombre_gasto'], fila['monto']
                c_txt, c_btn = st.columns([8, 2])
                with c_txt: st.write(f"💼 **{f_nombre}**: {f_monto:,.2f} € / mes")
                with c_btn:
                    if st.button("🗑️ Eliminar", key=f"del_rec_{f_id}"):
                        conexion = sqlite3.connect(DB_PATH, timeout=10); cursor = conexion.cursor()
                        # Borrado en cascada en el historial
                        cursor.execute("DELETE FROM movimientos_caja WHERE concepto = ?", (f"Fijo Automático: {f_nombre}",))
                        cursor.execute("DELETE FROM gastos_recurrentes WHERE id = ?", (f_id,))
                        conexion.commit(); conexion.close(); st.rerun()
                st.markdown("<hr style='margin:0.2rem 0px;'/>", unsafe_allow_html=True)

# ==========================================
# VISTA 4: COMPRAS A PLAZOS (CON BORRADO EN CASCADA)
# ==========================================
elif opcion_menu == "💳 Compras a Plazos":
    st.title("💳 Auditoría de Compras Financiadas a Plazos")
    st.subheader("Control analítico de amortizaciones aplicando la regla estricta de los 25 €")
    st.markdown("---")
    col_l, col_r = st.columns([4, 6])
    with col_l:
        st.header("🛒 Financiar Nueva Compra")
        with st.form("form_plazos"):
            art_nombre = st.text_input("Artículo / Compra").strip()
            art_total = st.number_input("Coste Total del Artículo (€)", min_value=1.0, step=50.0)
            art_meses = st.number_input("¿En cuántos meses lo financias?", min_value=1, step=1, value=12)
            if st.form_submit_button("Auditar y Registrar"):
                if art_nombre and art_total > 0:
                    cuota_proyectada = art_total / art_meses
                    if cuota_proyectada < 25.0:
                        st.error(f"🚨 **REGLA DE ORO ROTA:** La cuota es de {cuota_proyectada:.2f} €/mes. ¡No financies! Cuotas menores de 25€ se pagan a tocateja.")
                    else:
                        try:
                            conexion = sqlite3.connect(DB_PATH, timeout=10); cursor = conexion.cursor()
                            cursor.execute("INSERT INTO compras_plazos (articulo, monto_total, meses_totales, meses_pagados) VALUES (?, ?, ?, 0)", (art_nombre, art_total, art_meses))
                            conexion.commit(); conexion.close(); st.success("¡Financiación registrada!"); st.rerun()
                        except sqlite3.IntegrityError: st.error("Ya existe este artículo financiado.")
    with col_r:
        st.header("📜 Financiaciones Activas")
        conexion = sqlite3.connect(DB_PATH, timeout=10)
        df_plazos = pd.read_sql_query("SELECT id, articulo, monto_total, meses_totales, meses_pagados FROM compras_plazos", conexion)
        conexion.close()
        if not df_plazos.empty:
            for index, fila in df_plazos.iterrows():
                p_id, p_art, p_total, p_m_totales, p_m_pagados = fila['id'], fila['articulo'], fila['monto_total'], fila['meses_totales'], fila['meses_pagados']
                cuota_actual = p_total / p_m_totales
                st.write(f"💳 **{p_art}** | Cuota: **{cuota_actual:.2f} €/mes** | Restan: **{p_total - (cuota_actual * p_m_pagados):.2f} €** de {p_total:.2f} €")
                st.progress((p_m_pagados / p_m_totales))
                if st.button("🗑️ Eliminar Financiación", key=f"del_plazo_{p_id}"):
                    conexion = sqlite3.connect(DB_PATH, timeout=10); cursor = conexion.cursor()
                    # Borrado en cascada en el historial usando LIKE para cazar todas las cuotas de ese artículo
                    cursor.execute("DELETE FROM movimientos_caja WHERE concepto LIKE ?", (f"Cuota Plazo: {p_art} (%",))
                    cursor.execute("DELETE FROM compras_plazos WHERE id = ?", (p_id,))
                    conexion.commit(); conexion.close(); st.rerun()
                st.markdown("<hr style='margin:0.4rem 0px;'/>", unsafe_allow_html=True)
            st.metric(label="📉 Carga Total por Plazos este Mes", value=f"{total_cuotas_plazos:,.2f} € / mes")

# ==========================================
# VISTA 5: PREVISIONES Y PROYECTOS (CON BORRADO EN CASCADA)
# ==========================================
elif opcion_menu == "🔮 Previsiones y Proyectos":
    st.title("🔮 Consultor de Viabilidad y Planes Futuros")
    st.markdown("---")
    col_p1, col_p2 = st.columns(2)
    with col_p1: sueldo_base = st.number_input("Tu Nómina Fija Real (€)", min_value=0.0, value=1300.0)
    with col_p2: gastos_fijos_est = st.number_input("Estimación Media de Suministros Variables", min_value=0.0, value=150.0)
    
    capacidad_ahorroador_teorica = sueldo_base - gastos_fijos_est - total_recurrentes - total_cuotas_plazos
    st.info(f"💡 Capacidad de ahorro libre: **{capacidad_ahorroador_teorica:.2f} € / mes**.")
    st.markdown("---")
    st.header("✈️ Crear Nuevo Proyecto")
    with st.form("form_proyecto"):
        col_pr1, col_pr2, col_pr3 = st.columns(3)
        with col_pr1: proj_name = st.text_input("Nombre del Proyecto")
        with col_pr2: proj_target = st.number_input("Objetivo Total (€)", min_value=10.0)
        with col_pr3: proj_months = st.number_input("¿En cuántos meses?", min_value=1, step=1)
        if st.form_submit_button("Lanzar Proyecto") and proj_name and proj_target > 0:
            try:
                conexion = sqlite3.connect(DB_PATH, timeout=10); cursor = conexion.cursor()
                cursor.execute("INSERT INTO proyectos_futuros (nombre_proyecto, objetivo_total, meses_restantes) VALUES (?, ?, ?)", (proj_name, proj_target, proj_months))
                conexion.commit(); conexion.close(); st.rerun()
            except sqlite3.IntegrityError: st.error("Ya existe este proyecto.")
    st.markdown("---")
    conexion = sqlite3.connect(DB_PATH, timeout=10)
    df_proj = pd.read_sql_query("SELECT id, nombre_proyecto, objetivo_total, meses_restantes, ahorrado_acumulado FROM proyectos_futuros", conexion)
    conexion.close()
    if not df_proj.empty:
        for index, fila in df_proj.iterrows():
            p_id, p_nombre, p_target, p_meses, p_ahorrado = fila['id'], fila['nombre_proyecto'], fila['objetivo_total'], fila['meses_restantes'], fila['ahorrado_acumulado']
            faltan = p_target - p_ahorrado
            cuota = faltan / p_meses if p_meses > 0 else faltan
            
            st.subheader(f"🎯 Proyecto: {p_nombre}")
            st.progress(min(100.0, (p_ahorrado / p_target) * 100) / 100.0)
            c_m1, c_m2, c_m3 = st.columns(3)
            c_m1.metric(label="Objetivo Restante", value=f"{faltan:,.2f} €")
            c_m2.metric(label="Tiempo Restante", value=f"{p_meses} Meses")
            c_m3.metric(label="Cuota Requerida", value=f"{cuota:,.2f} €/mes")
            
            if cuota > capacidad_ahorroador_teorica: st.error("⚠️ **INVIABLE**")
            else: st.success("✅ ¡Viable!")
            
            col_add1, col_add2, col_add3 = st.columns([2, 5, 3])
            with col_add1: monto_ingresar_hucha = st.number_input(f"Abonar (€)", min_value=0.0, step=10.0, key=f"num_{p_id}")
            with col_add2:
                st.write("")
                if st.button(f"Confirmar Abono", key=f"btn_h_{p_id}") and monto_ingresar_hucha > 0:
                    if monto_ingresar_hucha <= saldo_banco:
                        conexion = sqlite3.connect(DB_PATH, timeout=10); cursor = conexion.cursor()
                        cursor.execute("UPDATE proyectos_futuros SET ahorrado_acumulado = ahorrado_acumulado + ? WHERE id = ?", (monto_ingresar_hucha, p_id))
                        cursor.execute("INSERT INTO movimientos_caja (fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago) VALUES (?, ?, ?, 'Gasto Habitual', 'Tarjeta/PayPal')", (datetime.now().strftime("%Y-%m-%d"), f"Abono hucha: {p_nombre}", monto_ingresar_hucha))
                        conexion.commit(); conexion.close(); st.rerun()
            with col_add3:
                st.write("")
                if st.button(f"🗑️ Eliminar Proyecto", key=f"del_proj_{p_id}"):
                    conexion = sqlite3.connect(DB_PATH, timeout=10); cursor = conexion.cursor()
                    # Borrado en cascada de todos los abonos a la hucha de este proyecto en el historial
                    cursor.execute("DELETE FROM movimientos_caja WHERE concepto = ?", (f"Abono hucha: {p_nombre}",))
                    cursor.execute("DELETE FROM proyectos_futuros WHERE id = ?", (p_id,))
                    conexion.commit(); conexion.close(); st.rerun()
            st.markdown("<hr style='margin:0.5rem 0px;'/>", unsafe_allow_html=True)

# ==========================================
# VISTA 6: LECTOR DE TICKETS IA (GEMINI)
# ==========================================
elif opcion_menu == "📷 Lector de Tickets IA":
    st.title("📷 Escáner de Tickets Inteligente")
    st.markdown("---")
    
    # Obtenemos la API Key desde los secrets de Streamlit Cloud
    api_key = st.secrets["GEMINI_API_KEY"]
    
    archivo_ticket = st.file_uploader("Sube la foto del ticket:", type=["jpg", "jpeg", "png"])
    if archivo_ticket is not None:
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
                    
                    # Limpieza avanzada del formato JSON
                    if "```json" in raw_text:
                        raw_text = raw_text.split("```json")[1]
                    raw_text = raw_text.rsplit("```", 1)[0]
                    
                    st.session_state['resultado_json_ticket'] = json.loads(raw_text.strip())
                    
                    if os.path.exists("temp_ticket.png"): 
                        os.remove("temp_ticket.png")
                    st.success("¡Análisis completado!")
                except Exception as e: 
                    st.error(f"Error procesando ticket: {e}")
                    
        if 'resultado_json_ticket' in st.session_state:
            datos = st.session_state['resultado_json_ticket']
            col_i1, col_i2 = st.columns(2)
            with col_i1: 
                super_detectado = st.selectbox("Supermercado:", LISTA_SUPERS, index=LISTA_SUPERS.index(datos['supermercado']) if datos['supermercado'] in LISTA_SUPERS else 0)
            with col_i2: 
                pago_detectado = st.selectbox("Pago:", ["Efectivo", "Tarjeta/PayPal"], index=0 if datos['metodo_pago'] == "Efectivo" else 1)
            
            col_l, col_r = st.columns(2)
            with col_l:
                df_desp = pd.DataFrame(datos['articulos_despensa'])
                if not df_desp.empty: st.dataframe(df_desp, use_container_width=True)
            with col_r:
                df_hogar = pd.DataFrame(datos['gastos_hogar'])
                if not df_hogar.empty: st.dataframe(df_hogar, use_container_width=True)
            
            if st.button("🔨 Inyectar Todo al Sistema"):
                conexion = sqlite3.connect(DB_PATH, timeout=10); cursor = conexion.cursor()
                fecha_actual = datetime.now().strftime("%Y-%m-%d")
                
                # Inyectar Despensa
                for item in datos['articulos_despensa']:
                    cursor.execute("INSERT INTO despensa (producto_generico, supermercado, unidades_actuales, peso_neto_kg, precio_unitario) VALUES (?, ?, ?, ?, ?)", 
                                   (item['producto'].lower().strip(), super_detectado, item['unidades'], item['peso_kg'], item['precio_unitario']))
                    cursor.execute("INSERT INTO movimientos_caja (fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago) VALUES (?, ?, ?, 'Gasto Habitual', ?)", 
                                   (fecha_actual, f"Alimento: {item['producto']}", item['unidades'] * item['precio_unitario'], pago_detectado))
                
                # Inyectar Bazar/Hogar
                for gasto in datos['gastos_hogar']:
                    cursor.execute("INSERT INTO movimientos_caja (fecha, concepto, monto, tipo_ingreso_gasto, metodo_pago) VALUES (?, ?, ?, 'Gasto Habitual', ?)", 
                                   (fecha_actual, f"Bazar: {gasto['concepto']}", gasto['precio_total'], pago_detectado))
                
                conexion.commit(); conexion.close(); del st.session_state['resultado_json_ticket']; st.rerun()
