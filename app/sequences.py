"""Definicion de secuencias y copy de emails.

KD_OWN_FUNNEL: funnel propio de Kong Designs (llamada de venta POST-demo).
  El visitante ya vio el demo (sitio, booking, emails muestra); la llamada
  es para hablar de implementacion: alcance, tiempos, precio.
  T+0 confirmacion (real), T-24h recordatorio (real),
  +1d post-llamada (real), +7d check-in (real).
  Copy en espanol, voz Kong Designs. PENDIENTE aprobacion de Alex.

DEMO_NURTURE: motor listo, secuencia DESHABILITADA y SIN copy final.
  Se activa solo con OK explicito de Alex al diseno.
  (Propuesta: T+0 confirmacion + boton a pagina de muestras,
   T+1/T+3/T+7 muestras en vivo rotuladas MUESTRA.)

Los templates usan {name}, {demo_date}, {demo_time}, {clinic}, {email}.
El HTML no lleva llaves fuera de esos placeholders.
Copy en ASCII seguro (sin acentos) por el patron de codificacion probado.
"""

T0_CONFIRM_SUBJECT = "Para aprovechar su llamada con Kong Designs"

T0_CONFIRM_HTML = """<p>Hola {name},</p>
<p>Ya quedo su llamada para el <b>{demo_date}</b> a las <b>{demo_time}</b> (la invitacion de calendario le llego por separado).</p>
<p>Ya vio el demo, asi que en esta llamada vamos directo a lo suyo: que quiere implementar en {clinic}, tiempos y precio.</p>
<p>Para aprovecharla al maximo, tenga a la mano: como reciben citas hoy y que es lo que mas les cuesta (llamadas perdidas, inasistencias, seguimiento).</p>
<p>Si necesita mover la llamada, responda este correo y la reagendamos.</p>
<p>Saludos,<br>Alex Kong<br>Director Creativo, Kong Designs<br>kongdesigns.com</p>"""

T24_REMINDER_SUBJECT = "Manana: su llamada con Kong Designs"

T24_REMINDER_HTML = """<p>Hola {name},</p>
<p>Recordatorio: manana <b>{demo_date}</b> a las <b>{demo_time}</b> platicamos de la implementacion para {clinic}.</p>
<p>Si tiene a la mano cuantos pacientes pierden al mes por llamadas no contestadas o inasistencias, mejor: asi hablamos de numeros reales.</p>
<p>Nos vemos manana.<br>Alex Kong, Kong Designs</p>"""

POST_1D_SUBJECT = "Siguiente paso para {clinic}"

POST_1D_HTML = """<p>Hola {name},</p>
<p>Ayer platicamos de implementar el sistema en {clinic}.</p>
<p>Le preparo esta semana la propuesta con alcance, tiempos y precio. Hay algo mas que quiera que incluya?</p>
<p>Saludos,<br>Alex Kong<br>Director Creativo, Kong Designs</p>"""

CHECKIN_7D_SUBJECT = "Seguimos?"

CHECKIN_7D_HTML = """<p>Hola {name},</p>
<p>Hace una semana platicamos de la implementacion para {clinic}. En que quedo?</p>
<p>Sin presion: si no es el momento, me dice y lo retomamos cuando quiera.</p>
<p>Saludos,<br>Alex Kong, Kong Designs</p>"""


def _step(key, offset, subject, html):
    return {"key": key, "offset": offset, "subject": subject, "html": html}


SEQUENCES = {
    "KD_OWN_FUNNEL": {
        "enabled": True,
        "description": "Funnel propio Kong Designs (venta post-demo): "
                       "confirmacion, recordatorio 24h, "
                       "post-llamada +1d, check-in +7d.",
        "steps": [
            _step("t0_confirm", "enroll+0s", T0_CONFIRM_SUBJECT,
                  T0_CONFIRM_HTML),
            _step("t24_reminder", "demo-24h", T24_REMINDER_SUBJECT,
                  T24_REMINDER_HTML),
            _step("post_1d", "demo+24h", POST_1D_SUBJECT, POST_1D_HTML),
            _step("checkin_7d", "demo+7d", CHECKIN_7D_SUBJECT,
                  CHECKIN_7D_HTML),
        ],
    },
    "DEMO_NURTURE": {
        # DESHABILITADA: diseno pendiente de OK de Alex.
        # No hay copy final; los placeholders se llenan al aprobarse.
        "enabled": False,
        "description": "Muestras en vivo del demo (T+0/T+1/T+3/T+7). "
                       "PENDIENTE diseno aprobado por Alex.",
        "steps": [
            _step("sample_0d", "enroll+0s", None, None),
            _step("sample_1d", "enroll+24h", None, None),
            _step("sample_3d", "enroll+72h", None, None),
            _step("sample_7d", "enroll+168h", None, None),
        ],
    },
}


def get_sequence(name):
    seq = SEQUENCES.get(name)
    if seq is None:
        raise KeyError(f"secuencia desconocida: {name}")
    return seq
