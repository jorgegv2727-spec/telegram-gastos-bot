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
    await update.message.reply_text("🤖 Asistente contable 24/7 activo. Envíame tus gastos.")

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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, registrar))

    print("🚀 Bot iniciado y corriendo 24/7...")
    app.run_polling()
