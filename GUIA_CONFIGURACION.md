# Guía de configuración: login con Google + Supabase

Tiempo aproximado: 20 minutos. Al terminar, el sitio tendrá:

- **Modo abierto**: cualquiera lo usa sin cuenta (como hoy).
- **Continuar con Google**: quien inicia sesión tiene "Mis pronósticos" (archivo, modelo, política y escenarios guardados).
- **Guardado permanente**: nada se pierde cuando Streamlit Cloud reinicia la app.

Orden recomendado: primero despliega el sitio en Streamlit Cloud (necesitas su dirección para Google), después Supabase, después Google, y al final pegas los secrets.

---

## 1. Desplegar en Streamlit Cloud

1. Sube el proyecto a un repositorio de GitHub (el `.gitignore` ya excluye `secrets.toml` y la carpeta local).
2. En https://share.streamlit.io → **Create app** → elige el repo, la rama y `app.py`.
3. En *App URL* elige la dirección, por ejemplo `motor-predictivo.streamlit.app`. **Anótala**.

## 2. Supabase (base de datos + archivos)

1. Entra a https://supabase.com y crea tu cuenta (lo más fácil: *Continue with GitHub*).
2. **New project**
   - Name: `motor-predictivo`
   - Database Password: genera una y guárdala
   - Region: **East US (North Virginia)** (Streamlit Cloud corre en EE.UU.)
3. Espera a que termine de crearse (1 a 2 minutos).
4. **Storage** → *New bucket*
   - Name: `motor`
   - *Public bucket*: **desactivado** (privado)
5. **SQL Editor** → *New query* → pega el contenido de `supabase.sql` → **Run**. Debe decir *Success*.
6. **Project Settings → API** (o *Data API*) y copia:
   - *Project URL* (algo como `https://abcd1234.supabase.co`)
   - *service_role* key (en *Project API keys*, botón *Reveal*). **Es secreta**: nunca en GitHub ni en mensajes.

> Plan gratis: 1 GB de archivos y 500 MB de base de datos. Si nadie usa el proyecto por una semana, Supabase lo
> pausa; se reactiva con un clic desde el panel.

## 3. Google (para "Continuar con Google")

1. Entra a https://console.cloud.google.com y crea un proyecto (arriba, selector de proyectos → *New project*).
2. **APIs y servicios → Pantalla de consentimiento de OAuth** (*Google Auth Platform*)
   - Tipo de usuario: **Externo**
   - Nombre de la app, tu correo de soporte y tu correo de contacto
   - Público / Audience: mientras esté en modo *Testing*, agrega como *Test users* los correos que van a probar.
     Para que cualquiera pueda entrar, pulsa **Publish app**.
3. **Credenciales → Crear credenciales → ID de cliente de OAuth**
   - Tipo de aplicación: **Aplicación web**
   - URI de redirección autorizados: `https://TU-APP.streamlit.app/oauth2callback`
     (y si quieres probar en tu computador, también `http://localhost:8501/oauth2callback`)
4. Copia el **Client ID** y el **Client secret**.

## 4. Pegar los secrets

En Streamlit Cloud → tu app → **Settings → Secrets**, pega el contenido de `.streamlit/secrets.toml.ejemplo`
reemplazando:

| Campo | Valor |
|---|---|
| `[auth] redirect_uri` | `https://TU-APP.streamlit.app/oauth2callback` |
| `[auth] cookie_secret` | una clave larga y aleatoria (por ejemplo 40 letras y números al azar) |
| `[auth] client_id` / `client_secret` | los de Google (paso 3) |
| `[almacen] url` / `key` | Project URL y service_role key de Supabase (paso 2) |

Guarda. La app se reinicia sola.

## 5. Comprobar

1. Abre el sitio: en la barra lateral debe aparecer **Continuar con Google** y en el menú **Mis pronósticos**.
2. Inicia sesión, carga un ejemplo y genera el pronóstico: debe decir *Guardado en Mis pronósticos*.
3. Entra con `?dev=1` al final de la dirección → **Detalles técnicos** → *Modelos guardados*: debe decir
   *Supabase Storage* y *Supabase (Postgres)*, y listar el modelo.
4. En Supabase → *Table Editor → pronosticos* debe aparecer la fila, y en *Storage → motor* las carpetas
   `modelos/` y `datos/`.

## 6. Datos en vivo (API en Render)

Opcional. Permite que un sistema (ERP, punto de venta, una planilla con script) envíe ventas e inventario nuevos a un
pronóstico guardado. El link a una planilla y el formulario funcionan sin este paso.

1. **Supabase → SQL Editor**: corre la parte final de `supabase.sql` (desde *Datos en vivo*). Crea las tablas
   `integraciones` y `datos_vivo`. Se puede correr más de una vez sin problema.
2. **Render** (render.com) → crea una cuenta con tu GitHub → *New → Blueprint* → elige el repo `motor-predictivo`.
   Render lee `render.yaml` y propone el servicio `motor-predictivo-api` (plan gratis).
3. Te pedirá dos variables (se escriben **solo en Render**, nunca en GitHub):
   - `SUPABASE_URL` = la misma `url` de `[almacen]`.
   - `SUPABASE_SERVICE_KEY` = la misma `key` (service_role) de `[almacen]`.
4. *Apply*. Cuando termine, abre `https://<tu-servicio>.onrender.com/salud`: debe responder `{"ok": true}`.
   En `/docs` está la documentación para quien vaya a conectar su sistema.
5. **Streamlit → Settings → Secrets**: agrega

   ```toml
   [api]
   url = "https://<tu-servicio>.onrender.com"
   ```

   Es solo la dirección (no es secreta); el sitio la usa para mostrar el ejemplo de envío.
6. Prueba: **Mis pronósticos → En vivo → Clave de integración → Crear clave**, copia la clave y envía una fila con
   el ejemplo `curl` que aparece ahí. Al abrir el pronóstico verás *Llegaron datos nuevos* y el botón
   **Actualizar pronóstico**.

En el plan gratis de Render el servicio se duerme tras unos minutos sin uso; el primer envío después de eso puede
tardar cerca de un minuto en responder.

## Seguridad, en corto

- Las contraseñas las maneja Google; el sitio nunca las ve.
- El sitio usa la service_role key solo en el servidor y **todas** las consultas filtran por el correo del usuario
  con sesión iniciada. La tabla tiene RLS activado sin políticas públicas: con la clave pública nadie puede leerla.
- Los archivos de cada usuario quedan en `datos/<identificador del usuario>/`, en un bucket privado.
- Los modelos (`modelos/`) se comparten por huella de datos: solo los recupera quien sube exactamente el mismo archivo.
- Las claves de integración se muestran una sola vez y se guardan solo como hash: si alguien ve la base de datos, no
  puede usarlas. Cada clave escribe únicamente en su propio pronóstico y se puede cambiar o desactivar cuando quieras.
- El link a una planilla solo acepta direcciones `https://` públicas.
