import os
import re
import json
from datetime import datetime
from aiohttp import web
from google import genai
from google.genai import types
import gspread
from google.oauth2.service_account import Credentials
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# 1. Variables de entorno
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
PORT = int(os.environ.get("PORT", 8080))

# 2. Conexión con Google Sheets
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
gc = gspread.authorize(credentials)
sh = gc.open("Control de Gastos")
worksheet = sh.sheet1
print(f"✅ Conectado a la hoja: '{sh.title}'")

# 3. Configuración Gemini AI
ai_client = genai.Client(api_key=GEMINI_API_KEY)

def extraer_monto_fallback(texto: str) -> float:
    match = re.search(r'(\d+[\.,]?\d*)', texto)
    if match:
        num_str = match.group(1).replace(',', '.')
        try:
            return float(num_str)
        except ValueError:
            return 0.0
    return 0.0

def clasificar_con_ia(texto: str):
    monto_respaldo = extraer_monto_fallback(texto)
    prompt = f"""
    Eres un asistente contable para gastos personales en España.
    Analiza este texto: "{texto}"

    Responde ÚNICAMENTE en formato JSON plano:
    {{
      "monto": float,
      "categoria": "Alimentación" | "Transporte" | "Vivienda/Hogar" | "Ocio/Compras" | "Salud" | "Suministros" | "Varios",
      "concepto": "resumen corto del gasto (máximo 3 palabras)"
    }}
    """
    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        datos = json.loads(response.text)
        monto = float(datos.get("monto", monto_respaldo))
        if monto == 0.0 and monto_respaldo > 0.0:
            monto = monto_respaldo
        return monto, datos.get("categoria", "Varios"), datos.get("concepto", texto)
    except Exception as e:
        print(f"⚠️ Error IA: {e}")
        return monto_respaldo, "Varios", texto

# 4. Manejadores de Telegram
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mensaje = (
        "🤖 *Asistente Contable 24/7 Activo*\n\n"
        "Comandos disponibles:\n"
        "• Escribe cualquier gasto en texto normal (ej: `Alcampo 15.40€`)\n"
        "• `/resumen` : Resumen y desglose de gastos del mes actual\n"
        "• `/ayuda` : Ver instrucciones de uso"
    )
    await update.message.reply_text(mensaje, parse_mode="Markdown")

async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mensaje = (
        "💡 *Cómo registrar gastos:*\n\n"
        "Solo escribe o di el concepto y el monto:\n"
        "• `Mercadona 23.50€`\n"
        "• `Cena hamburguesa 14`\n"
        "• `Metro billete 2.80`\n\n"
        "Usa `/resumen` para ver el balance mensual acumulado."
    )
    await update.message.reply_text(mensaje, parse_mode="Markdown")

async def resumen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_espera = await update.message.reply_text("📊 Calculando balance del mes...")
    
    try:
        filas = worksheet.get_all_values()
        if len(filas) <= 1:
            await msg_espera.edit_text("ℹ️ Todavía no hay gastos registrados en la hoja.")
            return

        mes_actual = datetime.now().strftime("%Y-%m")
        total_mes = 0.0
        totales_por_categoria = {}
        conteo_gastos = 0

        # Asume estructura: [Fecha, Categoría, Detalle, Monto, Fuente]
        for fila in filas[1:]:
            if len(fila) >= 4:
                fecha = fila[0].strip()
                cat = fila[1].strip() or "Varios"
                try:
                    # Limpia el monto de posibles símbolos de euro o comas
                    monto_str = fila[3].replace("€", "").replace(" ", "").replace(",", ".").strip()
                    monto = float(monto_str)
                except ValueError:
                    continue

                if fecha.startswith(mes_actual):
                    conteo_gastos += 1
                    total_mes += monto
                    totales_por_categoria[cat] = totales_por_categoria.get(cat, 0.0) + monto

        if conteo_gastos == 0:
            await msg_espera.edit_text(f"ℹ️ No hay gastos anotados en lo que va de este mes ({mes_actual}).")
            return

        iconos_cat = {
            "Alimentación": "🛒",
            "Transporte": "🛴",
            "Vivienda/Hogar": "🏠",
            "Ocio/Compras": "🎮",
            "Salud": "💊",
            "Suministros": "💡",
            "Varios": "📦"
        }

        nombre_mes = datetime.now().strftime("%B %Y").capitalize()
        texto_resumen = f"📊 *Resumen de Gastos - {nombre_mes}*\n\n"
        texto_resumen += f"💰 *Total acumulado:* `{total_mes:.2f} €` ({conteo_gastos} registros)\n\n"
        texto_resumen += "*Desglose por categoría:*\n"

        # Ordenar de mayor a menor gasto
        categorias_ordenadas = sorted(totales_por_categoria.items(), key=lambda x: x[1], reverse=True)

        for cat, subtotal in categorias_ordenadas:
            pct = (subtotal / total_mes) * 100 if total_mes > 0 else 0
            icono = iconos_cat.get(cat, "🔹")
            texto_resumen += f"{icono} *{cat}:* {subtotal:.2f} € ({pct:.1f}%)\n"

        await msg_espera.edit_text(texto_resumen, parse_mode="Markdown")

    except Exception as e:
        await msg_espera.edit_text(f"⚠️ Error al calcular resumen: {e}")

async def registrar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text
    fecha_hoy = datetime.now().strftime("%Y-%m-%d")
    
    mensaje_espera = await update.message.reply_text("🔍 Analizando con IA...")
    monto, categoria, concepto = clasificar_con_ia(texto)
    
    try:
        nueva_fila = [fecha_hoy, categoria, concepto, monto, "Gemini AI"]
        worksheet.append_row(nueva_fila)
        await mensaje_espera.edit_text(
            f"✅ *Gasto Registrado (24/7)*\n\n"
            f"📅 Fecha: {fecha_hoy}\n"
            f"🏷️ Categoría: *{categoria}*\n"
            f"📝 Detalle: {concepto}\n"
            f"💰 Monto: {monto:.2f} €",
            parse_mode="Markdown"
        )
    except Exception as e:
        await mensaje_espera.edit_text(f"⚠️ Error al guardar: {e}")

# 5. Servidor HTTP de soporte para Render (Health Check)
async def health_check(request):
    return web.Response(text="Bot 24/7 OK", status=200)

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"🌐 Servidor Web activo en puerto {PORT}")

async def post_init(application):
    await start_web_server()

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ayuda", ayuda))
    app.add_handler(CommandHandler("resumen", resumen))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, registrar))

    print("🚀 Bot iniciado y corriendo 24/7...")
    app.run_polling()
