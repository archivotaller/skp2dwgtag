# skp2dwg

Exporta **vistas 2D de SketchUp a DWG** conservando las **etiquetas (tags) como capas de AutoCAD**, incluyendo cortes.

## Aplicación de escritorio (sin Python ni OpenCode)

`dist\skp2dwg.exe` (35 MB) es la aplicación completa. Solo necesita SketchUp abierto y AutoCAD instalado.

- Acceso directo creado en el escritorio: **skp2dwg - SketchUp a DWG**.
- Muestra el estado de SketchUp y el modelo cargado.
- Botón **Iniciar servidor** que arranca el plugin por UI Automation, sin entrar al menú.
- Selección de vistas (planta, elevaciones, cortes y escenas).
- Carpeta de salida editable y YAML de capas opcional.
- Progreso por vista, registro y botones para abrir la carpeta o el DWG.

La aplicación no usa OpenCode para nada: habla directo con el plugin en `127.0.0.1:9876` y con `accoreconsole`.

### Descarga

- Última versión: <https://github.com/archivotaller/skp2dwgtag/releases/latest>
- Windows x64: `skp2dwg.exe` (no requiere Python).

Diagnóstico sin abrir la interfaz:

```powershell
dist\skp2dwg.exe --self-test
# escribe %TEMP%\skp2dwg_selftest.json con el estado del plugin,
# el arranque y los datos del modelo
```

```
SketchUp (modelo vivo)
   │  eval_ruby: cámara + visibilidad + geometría
   ▼
Export 2D DXF (HLR nativo de SketchUp)
   │  líneas en capa 0, exactas
   ▼
Atribución geométrica (proyección ortográfica de las aristas reales)
   │  cada línea → etiqueta de origen
   ▼
DXF con una capa por etiqueta + colores + capas ocultas congeladas
   │  accoreconsole (AutoCAD)
   ▼
DWG 2018 + auditoría (capas, capa 0 vacía, unidades)
```

## Por qué existe

- El export **3D** de SketchUp sí convierte etiquetas en capas, pero no produce vistas 2D.
- El export **2D** produce líneas exactas con línea oculta y cortes, pero **todo cae en la capa 0**.
- Este proyecto usa el 2D nativo para la geometría y atribuye cada línea a su etiqueta proyectando las aristas del modelo con la misma cámara.

Resultado probado: **97/97 líneas atribuidas, 0 en capa 0, error de proyección ≈ 0**.

## Requisitos

- SketchUp Pro 2024 (o superior) con el plugin **MCP Server for SketchUp 0.3.0** y el servidor TCP iniciado en `127.0.0.1:9876`.
- `eval_ruby` habilitado (viene habilitado por defecto).
- AutoCAD 2026 (o cualquier versión con `accoreconsole.exe`).
- Python 3.10+.

## Instalación

```powershell
# desde la raíz del proyecto
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
```

Comprobar:

```powershell
skp2dwg doctor
```

## Preparación única del exportador 2D

La primera vez (o si se restablecen las preferencias de SketchUp):

1. `Archivo > Exportar > Gráfico 2D...`
2. `Opciones...`
3. Versión de AutoCAD: **2018**.
4. Marcar **Tamaño real (1:1)**. Sin esto, la proyección no coincide y las líneas quedan sin etiqueta.
5. En `Líneas de sección`: **Polilíneas con ancho** (opcional; si se deja en Ninguno, la herramienta calcula el corte igualmente).
6. Aceptar y cerrar el diálogo.

La herramienta fuerza el modo 2D del exportador, pero no puede cambiar la escala desde Ruby.

## Uso

```powershell
# Exportar planta + 4 elevaciones + una vista por cada plano de sección
skp2dwg export

# Elegir carpeta de salida
skp2dwg export --out "C:\obra\planos"

# Exportar solo una vista
skp2dwg export --only PLANTA

# Usar las escenas del modelo
skp2dwg export --scenes

# Usar una definición propia de vistas
skp2dwg export --views config\views.example.yaml

# No activar las etiquetas ocultas durante el export
skp2dwg export --visible-only

# Overrides de capas (nombre, color, grosor, congelar)
skp2dwg export --layers config\layers.example.yaml
```

Salida por defecto: `<carpeta del .skp>\_DWG_skp2dwg\<fecha-hora>\`.

Cada vista produce:

- `<VISTA>.dwg` — entrega final.
- `<VISTA>.dxf` — intermedio editable.
- `_work/` — DXF crudo de SketchUp, aristas y snapshot del estado.
- `manifest.json` — datos completos de la corrida.
- `report.md` — tabla de líneas, etiquetas, cortes y verificación.

## Vistas

Sin `--views` ni `--scenes` se generan:

- `PLANTA` (superior, ortogonal)
- `ELEV_FRONTAL`, `ELEV_POSTERIOR`, `ELEV_DERECHA`, `ELEV_IZQUIERDA`
- `CORTE_<nombre>` por cada plano de sección del modelo

Formato de `views.yaml`:

```yaml
views:
  - name: PLANTA
    kind: top
  - name: CORTE_AA
    kind: section
    plane: "Sección 9"
    flip: false
  - name: ESCENA_1
    kind: scene
```

`kind`: `top`, `front`, `back`, `left`, `right`, `section`, `scene`.

## Capas

- Una capa por etiqueta, con su nombre original (saneado para DWG: `< > / \ " : ; ? * | = \`` → `_`).
- Color real de la etiqueta de SketchUp (true color).
- Grosor configurable (por defecto 0,25 mm).
- Etiquetas ocultas en SketchUp: su geometría **se incluye** durante el export y la capa queda **apagada y congelada** en el DWG.
- Capas auxiliares: `SIN_ETIQUETA`, `CORTE`, `PERFIL`, `TEXTO`.
- La capa 0 queda vacía.

`layers.yaml`:

```yaml
defaults:
  lineweight: 25        # centésimas de mm
layers:
  "muros":
    name: "ARQ_MUROS"
    color: [200, 30, 30]
    lineweight: 35
  "cajon":
    hidden: true
fallback:
  unmatched: "SIN_ETIQUETA"
  section: "CORTE"
  profile: "PERFIL"
  text: "TEXTO"
```

## Cortes

El exportador 2D de SketchUp no dibuja el plano de corte. La herramienta lo calcula intersectando el plano de sección con las caras trianguladas del modelo y lo agrega a la capa `CORTE`, ya proyectado con la cámara de la vista.

## Comandos sueltos

```powershell
# Reconstruir capas desde un DXF 2D y un edges.json ya existentes
skp2dwg build --dxf raw.dxf --edges edges.json --out final.dxf --model-info model.json

# DXF → DWG
skp2dwg to-dwg --dxf final.dxf --dwg final.dwg

# DWG → DXF (auditoría)
skp2dwg from-dwg --dwg final.dwg --dxf final.dxf

# Auditar capas de un DWG o DXF
skp2dwg verify --dwg final.dwg --expected "Layer0,madera,cajon,CORTE,SIN_ETIQUETA"
```

## Cómo funciona (detalle)

1. `eval_ruby` hace un snapshot del estado (cámara, visibilidad y color de etiquetas, sección activa, opciones de render).
2. Fuerza el exportador headless de SketchUp al modo 2D abriendo y cerrando el diálogo "Exportar gráfico 2D" (SketchUp recuerda el último exportador usado).
3. Ajusta cámara ortogonal, activa la sección y pone visibles todas las etiquetas objetivo.
4. Extrae todas las aristas con su etiqueta efectiva (la más interna, ignorando `Layer0`).
5. Exporta el DXF 2D con `model.export` (HLR nativo, 1:1).
6. Proyecta las aristas con la cámara real del export y asigna cada línea a la arista coincidente. Desempate: la arista más cercana a la cámara y, si empatan, la etiqueta no `Layer0`.
7. Construye el DXF final con capas, colores, grosores y estados.
8. Convierte a DWG 2018 con `accoreconsole` y audita reabriendo el DWG.
9. Restaura el estado del modelo. **Nunca guarda el .skp.**

## Limitaciones

- Vistas ortogonales (el matching asume proyección paralela).
- Sin bloques ni sombreados/hatches: el 2D nativo entrega líneas.
- Los textos y cotas van a `TEXTO` (no se atribuyen por etiqueta).
- Las carpetas de etiquetas de SketchUp se aplanan: la capa usa el nombre de la etiqueta.
- Si dos etiquetas tienen geometría exactamente coincidente, gana la más cercana a la cámara.
- El modo 2D del exportador es estado de sesión: si algo hace un export 3D, la herramienta lo detecta y lo corrige.

## Verificación realizada

Modelo de prueba (3 etiquetas, 544 aristas, 950 mm de alto):

| Prueba | Resultado |
|---|---|
| PLANTA | 97/97 líneas atribuidas, 0 en capa 0, DWG auditado |
| 5 vistas automáticas | 0 líneas sin etiqueta, ~2,4 s por vista |
| Corte horizontal | 96 segmentos en capa `CORTE` |
| Etiqueta oculta | geometría incluida, capa apagada y congelada, visibilidad restaurada |
| Estado del modelo | cámara, colores, visibilidad y secciones restaurados |
| Pruebas unitarias | 12/12 |
| Escala sintética | 100.000 aristas indexadas en 0,5 s; 10.000 líneas ~22 s |

## Problemas frecuentes

**`SketchUp no está escuchando en 127.0.0.1:9876`**
Abre SketchUp y ejecuta `Extensiones > MCP Server > Start Server`.

**Handshake rechazado**
El plugin 0.3.0 solo acepta cliente 0.3.0. No actualices `sketchup-mcp2` a 0.4 sin actualizar el plugin.

**Muchas líneas sin etiqueta**
Comprueba que la vista sea ortogonal, la escala 1:1 y que el modelo no esté en perspectiva. El reporte indica el porcentaje.

**accoreconsole se cuelga**
Suele ser un diálogo de sobrescritura o un script mal formado. La herramienta borra el destino y escribe el script en bytes con CRLF.

## Seguridad

- No modifica archivos `.skp`.
- Restaura cámara, visibilidad, colores, sección y opciones de render al terminar.
- Todo el trabajo ocurre en la carpeta de salida elegida.
