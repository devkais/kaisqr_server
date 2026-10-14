# Cómo conectar un sistema nuevo al escáner QR

Esta guía explica cómo conectar otro sistema —por ejemplo, `biblioteca`— para que sus usuarios puedan subir documentos desde la computadora o desde la app móvil.

## La idea, en simple

Imaginá que el QR Server es una oficina de correo:

- El **frontend** abre una sala y muestra un QR y un código.
- La **app móvil** entra a esa sala y entrega los documentos.
- El **QR Server** guarda los archivos por un ratito y después se los manda al backend correcto.
- El **backend del sistema** recibe los documentos y decide cómo guardarlos temporalmente y cuándo relacionarlos con el trámite correspondiente.

Para que el correo sepa adónde llevar cada paquete, hay que enseñarle cómo hablar con el backend nuevo. En este proyecto eso se hace con un **adaptador**.

Algunas palabras que vas a encontrar:

- **Adaptador:** un traductor que convierte el pedido del QR Server en un pedido que entiende el backend nuevo.
- **Endpoint:** una dirección o “puerta” de la API donde se reciben pedidos.
- **Documento temporal:** un documento guardado en una caja de espera, antes de quedar asociado definitivamente a un trámite.

## Antes de empezar: dos llaves distintas

Hay dos credenciales y no deben confundirse:

| Llave | Quién la usa | Para qué sirve |
|---|---|---|
| `API_KEY` del QR Server | Frontend, app móvil y cualquier backend que llame operaciones protegidas | Les permite usar algunas operaciones del QR Server, como crear una sala o unirse por código. Se envía como `X-API-Key`. |
| `SISTEMA_SERVICE_KEY` | QR Server y backend del sistema | Permite que el QR Server entregue los documentos al backend. Es privada y nunca va en el frontend ni en la app móvil. |

La `API_KEY` que se configura en el frontend queda dentro del código que recibe el navegador. No se debe tratar como una contraseña secreta. La `SERVICE_KEY`, en cambio, sí es un secreto de servidor a servidor: **no la pongás en una variable `VITE_...`**.

## Paso 1: decidir cómo se va a identificar el destino

Antes de programar, el equipo del frontend y el backend debe acordar:

1. El nombre del sistema, por ejemplo `biblioteca`. Usá el mismo nombre en todas partes; en el QR Server debe coincidir con el nombre del adaptador.
2. El módulo que recibirá los archivos, por ejemplo `solicitudes`.
3. Cómo se identifica el trámite o recurso. El QR Server pide **exactamente una** de estas opciones:
   - `recurso_id`: un número, por ejemplo `123`.
   - `recurso_referencia`: un UUID, por ejemplo `a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11`.
4. Qué dirección y ruta tendrá el endpoint del backend que recibe los documentos.
5. Qué encabezado HTTP llevará la `SERVICE_KEY`. El adaptador y el backend deben acordar el mismo. Por ejemplo, el adaptador actual de Transporte usa `X-KaisQr-Service-Key`.

## Paso 2: preparar el backend del sistema

El backend nuevo debe:

1. Guardar la `SERVICE_KEY` en su configuración de servidor, con el mismo valor que se usará en el QR Server. No la escribas directamente en el código.
2. Crear una entidad o tabla de **documentos temporales**. Al recibirlos, todavía no tienen por qué estar ligados al trámite definitivo ni ejecutar la lógica normal del sistema.
3. Crear un endpoint privado que reciba los archivos y los datos acordados. Según el flujo del sistema, también puede necesitar un endpoint para confirmar que ya llegaron todos los archivos de una sala.
4. Validar la `SERVICE_KEY` en cada solicitud del QR Server y rechazar credenciales incorrectas.
5. Responder con éxito solo cuando el documento se haya recibido correctamente. Si falla, devolver un error para que el QR Server no marque la entrega como exitosa.

Los dos backends tienen que hablar el mismo idioma: misma URL, ruta, encabezado de autenticación, nombres de campos y formato de respuesta. Si uno envía `archivo` y el otro espera `documento`, no se van a entender.

## Paso 3: configurar y enseñar el QR Server

### Variables de entorno

Agregá la configuración del sistema en el entorno del QR Server. Por ejemplo:

```env
BIBLIOTECA_FORWARD_ENABLED=true
BIBLIOTECA_BASE_URL=https://biblioteca.ejemplo.com/api
BIBLIOTECA_SERVICE_KEY=<la-misma-clave-configurada-en-el-backend>
```

Reemplazá `BIBLIOTECA` por el nombre del sistema en mayúsculas. La URL debe apuntar a la base de la API; la ruta completa para subir documentos la define el adaptador.

**Importante:** agregar estas líneas al `.env` no alcanza por sí solo. El QR Server actual conoce sus variables y adaptadores explícitamente. También hay que:

1. Agregar los campos correspondientes a `Settings` en `app/core/config.py`.
2. Crear un adaptador nuevo en `app/infrastructure/destinations/`. El adaptador debe:
   - identificarse con el nombre acordado, por ejemplo `biblioteca`;
   - comprobar que el módulo y el recurso sean válidos para ese sistema;
   - enviar cada archivo al endpoint del backend, usando la `SERVICE_KEY` acordada;
   - informar si la entrega terminó bien o si falló.
3. Registrar ese adaptador en la lista de `DestinationDispatcher` de `app/main.py`.
4. Agregar el dominio del frontend a `CORS_ORIGINS` si el frontend corre en otro origen.

La interfaz que debe cumplir el adaptador está definida por `DestinationAdapter`. Podés mirar los adaptadores de `kaisvm` y `transporte` como ejemplos, pero no copies su contrato a ciegas: cada backend puede recibir los documentos de una manera distinta.

Decidí también qué significa `FORWARD_ENABLED=false` para el sistema nuevo. En los adaptadores actuales el comportamiento no es igual: KaisVM puede reportar éxito sin reenviar y Transporte considera que la entrega está desactivada como un error. Para probar una entrega real, usá `true` y verificá que el backend haya recibido el documento.

## Paso 4: apuntar la app móvil al QR Server

La app móvil debe tener configurada:

- La URL del QR Server al que se conecta.
- La misma `API_KEY` configurada como `API_KEY` en el QR Server.

Si las llaves no coinciden, la app no podrá usar las operaciones protegidas. La app móvil no necesita la `BIBLIOTECA_SERVICE_KEY`: esa llave solo se usa entre servidores.

## Paso 5: configurar el frontend del sistema

Configurá estas tres variables en el frontend:

```env
VITE_URL_KAISQR=https://qr.ejemplo.com/api/v1
VITE_URL_KAISQR_WS=wss://qr.ejemplo.com/api/v1/ws/v1
VITE_KAISQR_API_KEY=<la-API_KEY-del-QR-Server>
```

- `VITE_URL_KAISQR`: dirección HTTP del QR Server. En este proyecto, el prefijo de API por defecto es `/api/v1`.
- `VITE_URL_KAISQR_WS`: dirección base del WebSocket. La ruta del proyecto incluye `/api/v1/ws/v1`; el QR Server agrega después la ruta de la sala. Configurá también `PUBLIC_WEBSOCKET_URL` del QR Server para que coincida con esa dirección base y sea accesible desde el navegador.
- `VITE_KAISQR_API_KEY`: la misma `API_KEY` configurada en el QR Server.

Usá `https://` y `wss://` en producción. `http://` y `ws://` son para desarrollo local. Después de cambiar variables `VITE_...`, normalmente hay que volver a construir o iniciar el frontend para que tome los valores nuevos.

**Nunca agregues `BIBLIOTECA_SERVICE_KEY` al frontend.** Las variables `VITE_...` se entregan al navegador.

## Paso 6: agregar la elección de cómo subir

Al iniciar la carga, el frontend debe ofrecer dos opciones:

### Subir desde la computadora

El usuario elige archivos en su computadora y sigue el flujo de carga local que ya tiene el sistema. Esta opción no usa la app móvil.

### Subir con la app móvil

1. El frontend pide al QR Server que cree una sala. Envía el nombre del sistema y módulo, y exactamente uno de `recurso_id` o `recurso_referencia`.
2. El QR Server devuelve un QR y un código de seis dígitos. El frontend muestra ambos.
3. La persona abre la app móvil y escanea el QR o escribe el código.
4. Desde el móvil, selecciona o captura los documentos y los envía a la sala.
5. El frontend muestra el avance de la sala mediante WebSocket.
6. Al terminar, la sala entrega los documentos al adaptador del sistema, y el adaptador los envía al backend.

Las llamadas para crear la sala y unirse por código necesitan la `API_KEY` en el encabezado `X-API-Key`. Después de entrar a la sala, se usan los tokens de sesión que entrega el QR Server; esos tokens son distintos de la `API_KEY` y de la `SERVICE_KEY`.

## Paso 7: probar todo, de punta a punta

Hacé una prueba con un documento que no sea importante:

- [ ] El QR Server inicia con la configuración nueva y el reenvío está habilitado.
- [ ] El frontend abre la pantalla para elegir carga local o móvil.
- [ ] La opción móvil crea una sala y muestra el QR y el código.
- [ ] La app móvil puede entrar usando el QR o el código.
- [ ] El móvil puede enviar un documento y el frontend ve el avance.
- [ ] Al finalizar, el backend recibe el documento temporal y confirma que quedó guardado.
- [ ] Si se usa una `SERVICE_KEY` incorrecta o el backend está apagado, la entrega aparece como fallida; no como exitosa.
- [ ] La `SERVICE_KEY` no aparece en el código del frontend, en la app móvil ni en los registros públicos.

Después de cambiar variables del QR Server, reiniciá el servicio: su configuración se carga al iniciar. Las salas del QR Server se guardan en memoria y no sobreviven a un reinicio, así que hacé la prueba completa sin reiniciar a mitad de camino.

## Mapa rápido de archivos del QR Server

| Archivo o carpeta | Para qué sirve |
|---|---|
| `app/core/config.py` | Declara las variables de entorno que entiende el QR Server. |
| `app/infrastructure/destinations/` | Contiene el protocolo y los adaptadores que envían documentos a cada sistema. |
| `app/main.py` | Crea y registra los adaptadores disponibles. |
| `app/presentation/controllers/sala_controller.py` | Define las rutas HTTP y la validación de la creación de salas. |
| `app/application/services/gestion_salas.py` | Administra salas, documentos, WebSocket y la entrega final al destino. |
