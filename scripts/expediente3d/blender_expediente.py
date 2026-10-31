# -*- coding: utf-8 -*-
"""Motor 3D de «Expedientes» (historias documentales) — Blender 5.2, Cycles + OptiX.

Un «set» con 5 ESTACIONES en círculo; cada beat del guion dice en qué estación está la cámara y qué
EFECTOS ocurren, anclados a la PALABRA exacta de la voz (linea_tiempo.json):
  racks     → sala de servidores (LEDs verde→rojo «caida», «apagado», «recuperacion»)
  globo     → planeta de puntos que se encienden desde un origen («propagar»)
  barras    → columnas de datos MEDIDOS (guion.datos.barras.valores) que crecen palabra por palabra («crecer»)
  linea     → línea de tiempo de marcas que se encienden una por una («marcar»)
  monolito  → pantalla flotante (la capa 2D escribe encima); «alerta» la pone roja
  espacio   → planeta + nave + trayectorias (22-oct-2026), con cámara y estrellas propias, lejos del set
Sin cuadrícula (operador, 14-oct): piso oscuro liso y reflejante con halo. Texto, cifras, fuentes y firma
van en la capa 2D (capa_expediente3d.py), no aquí.

Uso:  <blender_env>\\python.exe blender_expediente.py <dir_pieza> [samples]
      CUADRO=120,900 → solo esos cuadros a <dir>/prueba/   ·  reanudable: no rehace cuadros existentes
Formato (17-oct-2026, documental 16:9; los defaults dejan todo igual que antes = vertical 1080×1920):
      ANCHO=1920 ALTO=1080   resolución       ·  RANGO=0-480  solo esos cuadros del render completo
      CAM_SHIFT_X=-0.18      desplaza el encuadre (el sujeto queda a un lado y la capa 2D usa el otro)
      LENTE=0.75             multiplica la focal (en 16:9 el campo vertical es más estrecho)
"""
import json
import math
import os
import sys
from pathlib import Path

import bpy
import bmesh  # noqa: E402  (bmesh existe solo tras importar bpy)
from mathutils import Vector  # noqa: E402  (estación espacio)

D = Path(sys.argv[1]).resolve()
SAMPLES = int(sys.argv[2]) if len(sys.argv) > 2 else 32
TL = json.loads((D / "linea_tiempo.json").read_text(encoding="utf-8"))
G = json.loads((D / "guion.json").read_text(encoding="utf-8"))
FPS, NF = TL["fps"], TL["frames"]
BT = {b["id"]: b for b in TL["beats"]}
GB = {b["id"]: b for b in G["beats"]}
DATOS = G.get("datos", {})


def norm(w):
    return w.strip("¿?¡!.,;:«»\"'()").lower()


def cuadro(beat, palabra=None, n=1, fin=False, desfase=0.0):
    """Cuadro de la n-ésima `palabra` del beat; sin palabra: inicio del beat + desfase (s)."""
    if palabra:
        k = 0
        for p in TL["palabras"]:
            if p["beat"] == beat and norm(p["w"]) == norm(palabra):
                k += 1
                if k == n:
                    return round(((p["fin"] if fin else p["ini"]) + desfase) * FPS)
        print("AVISO: «%s» #%d no está en %s; uso el inicio del beat" % (palabra, n, beat))
    return round((BT[beat]["ini"] + desfase) * FPS)


# ── render ──────────────────────────────────────────────────────────────────
bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene
sc.render.engine = "CYCLES"
sc.cycles.device = "GPU"
pref = bpy.context.preferences.addons["cycles"].preferences
pref.compute_device_type = "OPTIX"
pref.get_devices()
for d in pref.devices:
    d.use = d.type == "OPTIX"
sc.cycles.samples = SAMPLES
sc.cycles.max_bounces, sc.cycles.diffuse_bounces, sc.cycles.glossy_bounces = 6, 2, 3
sc.cycles.transmission_bounces, sc.cycles.transparent_max_bounces = 2, 4
sc.cycles.use_denoising = True
sc.cycles.denoiser = "OPTIX"
sc.render.use_persistent_data = True
sc.render.resolution_x, sc.render.resolution_y = int(os.environ.get("ANCHO", 1080)), int(os.environ.get("ALTO", 1920))
sc.render.fps = FPS
sc.frame_start, sc.frame_end = 0, NF - 1
sc.view_settings.view_transform = "AgX"
sc.view_settings.look = "AgX - Punchy"

ROJO, VERDE, CIAN, AMBAR = (1.0, 0.12, 0.08, 1), (0.1, 1.0, 0.45, 1), (0.15, 0.85, 1.0, 1), (1.0, 0.6, 0.1, 1)


def mat_emisivo(nombre, color, fuerza):
    m = bpy.data.materials.new(nombre)
    nt = m.node_tree
    nt.nodes.clear()
    e = nt.nodes.new("ShaderNodeEmission")
    e.inputs["Color"].default_value = color
    e.inputs["Strength"].default_value = fuerza
    nt.links.new(e.outputs[0], nt.nodes.new("ShaderNodeOutputMaterial").inputs[0])
    return m, e


def mat_metal(nombre, color=(0.09, 0.1, 0.12, 1), rug=0.3):
    m = bpy.data.materials.new(nombre)
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = color
    p.inputs["Metallic"].default_value = 0.85
    p.inputs["Roughness"].default_value = rug
    return m


def mat_estado(nombre, c0, c1, fuerza):
    """Emisión que mezcla c0→c1 según un nodo Valor «estado» (animable) y un nodo «brillo»."""
    m = bpy.data.materials.new(nombre)
    nt = m.node_tree
    nt.nodes.clear()
    est = nt.nodes.new("ShaderNodeValue")
    est.name = "estado"
    est.outputs[0].default_value = 0.0
    br = nt.nodes.new("ShaderNodeValue")
    br.name = "brillo"
    br.outputs[0].default_value = fuerza
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.inputs["A"].default_value, mix.inputs["B"].default_value = c0, c1
    nt.links.new(est.outputs[0], mix.inputs["Factor"])
    e = nt.nodes.new("ShaderNodeEmission")
    nt.links.new(mix.outputs["Result"], e.inputs["Color"])
    nt.links.new(br.outputs[0], e.inputs["Strength"])
    nt.links.new(e.outputs[0], nt.nodes.new("ShaderNodeOutputMaterial").inputs[0])
    return m


def key_val(mat, nodo, pares):
    """pares = [(cuadro, valor), …] sobre un nodo Valor del material."""
    s = mat.node_tree.nodes[nodo].outputs[0]
    for fr, v in pares:
        s.default_value = v
        s.keyframe_insert("default_value", frame=max(0, fr))


def caja(nombre, loc, dims, mat):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    o = bpy.context.active_object
    o.name = nombre
    o.scale = dims
    o.data.materials.append(mat)
    return o


# ── set: 5 estaciones en FILA sobre el eje x, todas mirando a −y (hacia la cámara) ──
# (15-oct: en círculo la cámara veía la estación vecina y el reverso de los objetos)
ESTACIONES = ["racks", "globo", "barras", "linea", "monolito"]
SEP = 30.0
CENTRO = {e: ((i - 2) * SEP, 0.0, 0.0) for i, e in enumerate(ESTACIONES)}
ANG = {e: 0.0 for e in ESTACIONES}


def efectos(estacion):
    """[(beat, efecto)] de todos los beats que piden algo en esta estación."""
    return [(b["id"], ef) for b in G["beats"] if b.get("estacion") == estacion for ef in b.get("efectos", [])]


# racks: 3 filas × 5 racks, LEDs con estado verde→rojo
def construir_racks(c):
    metal = mat_metal("rack")
    leds = []
    for fila in range(3):
        for k in range(5):
            x, y = c[0] + (k - 2) * 1.25, c[1] + (fila - 1) * 2.4
            caja("rack", (x, y, 1.15), (0.95, 0.75, 2.3), metal)
            m = mat_estado("led_%d_%d" % (fila, k), VERDE, ROJO, 6.0)
            for j in range(7):
                caja("led", (x, y - 0.38, 0.35 + j * 0.3), (0.7, 0.02, 0.05), m)
            leds.append((abs(k - 2) + fila, m))
    for beat, ef in efectos("racks"):
        f0 = cuadro(beat, ef.get("palabra"), ef.get("n", 1), desfase=ef.get("desfase", 0))
        for dist, m in leds:
            fd = f0 + dist * 4  # ola desde el centro
            if ef["tipo"] == "caida":
                key_val(m, "estado", [(fd - 1, 0.0), (fd + 6, 1.0)])
                key_val(m, "brillo", [(fd - 1, 6.0), (fd + 3, 14.0), (fd + 10, 7.0)])
            elif ef["tipo"] == "apagado":
                key_val(m, "brillo", [(fd - 1, 7.0), (fd + 8, 0.15)])
            elif ef["tipo"] == "recuperacion":
                key_val(m, "estado", [(fd - 1, 1.0), (fd + 10, 0.0)])
                key_val(m, "brillo", [(fd - 1, 0.15), (fd + 10, 6.0)])


# globo: puntos de Fibonacci en una sola malla; se encienden por distancia a un origen
def construir_globo(c):
    R, N = 2.6, 900
    cz = 3.2
    bpy.ops.mesh.primitive_uv_sphere_add(radius=R * 0.97, location=(c[0], c[1], cz), segments=64, ring_count=32)
    nucleo = bpy.context.active_object
    nucleo.data.materials.append(mat_metal("nucleo", (0.01, 0.015, 0.03, 1), 0.25))
    me = bpy.data.meshes.new("puntos")
    bm = bmesh.new()
    dist_attr = bm.verts.layers.float.new("dist")
    ox = (0.35, -0.6, 0.72)  # origen de la propagación (normalizado aprox.)
    golden = math.pi * (3 - math.sqrt(5))
    for i in range(N):
        yv = 1 - 2 * (i + 0.5) / N
        r = math.sqrt(1 - yv * yv)
        th = golden * i
        p = (math.cos(th) * r, yv, math.sin(th) * r)
        d = math.acos(max(-1, min(1, sum(a * b for a, b in zip(p, ox)) / 1.0)))
        ret = bmesh.ops.create_icosphere(bm, subdivisions=1, radius=0.035)
        for v in ret["verts"]:
            v.co = (v.co.x + c[0] + p[0] * R, v.co.y + c[1] + p[1] * R, v.co.z + cz + p[2] * R)
            v[dist_attr] = d
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("puntos", me)
    sc.collection.objects.link(ob)
    m = bpy.data.materials.new("puntos")
    nt = m.node_tree
    nt.nodes.clear()
    at = nt.nodes.new("ShaderNodeAttribute")
    at.attribute_name = "dist"
    rev = nt.nodes.new("ShaderNodeValue")
    rev.name = "estado"
    rev.outputs[0].default_value = -1.0
    lt = nt.nodes.new("ShaderNodeMath")
    lt.operation = "LESS_THAN"
    nt.links.new(at.outputs["Fac"], lt.inputs[0])
    nt.links.new(rev.outputs[0], lt.inputs[1])
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.inputs["A"].default_value = CIAN
    mix.inputs["B"].default_value = ROJO
    nt.links.new(lt.outputs[0], mix.inputs["Factor"])
    e = nt.nodes.new("ShaderNodeEmission")
    e.inputs["Strength"].default_value = 9.0
    nt.links.new(mix.outputs["Result"], e.inputs["Color"])
    nt.links.new(e.outputs[0], nt.nodes.new("ShaderNodeOutputMaterial").inputs[0])
    ob.data.materials.append(m)
    for beat, ef in efectos("globo"):
        if ef["tipo"] == "propagar":
            f0 = cuadro(beat, ef.get("palabra"), ef.get("n", 1), desfase=ef.get("desfase", 0))
            dur = round(ef.get("segundos", 2.5) * FPS)
            key_val(m, "estado", [(f0 - 1, -0.01), (f0 + dur, math.pi + 0.1)])
    # giro lento todo el video
    for o in (ob, nucleo):
        o.rotation_mode = "XYZ"
    piv = bpy.data.objects.new("giro_globo", None)
    piv.location = (c[0], c[1], cz)
    sc.collection.objects.link(piv)
    for o in (ob, nucleo):
        o.parent = piv
        o.location = (o.location[0] - c[0], o.location[1] - c[1], o.location[2] - cz)
    piv.rotation_euler = (0, 0, 0)
    piv.keyframe_insert("rotation_euler", frame=0)
    piv.rotation_euler = (0, 0, math.radians(40))
    piv.keyframe_insert("rotation_euler", frame=NF - 1)


# barras: valores MEDIDOS del guion (escala lineal o log), crecen en las palabras indicadas
def construir_barras(c):
    cfg = DATOS.get("barras", {})
    vals = cfg.get("valores") or [1, 2, 3]
    if cfg.get("escala") == "log":
        lo = math.log10(min(vals))
        alt = [0.6 + 4.2 * (math.log10(v) - lo) / max(1e-9, math.log10(max(vals)) - lo) for v in vals]
    else:
        alt = [0.4 + 4.4 * v / max(vals) for v in vals]
    n = len(vals)
    cols = []
    for i, h in enumerate(alt):
        x = c[0] + (i - (n - 1) / 2) * 1.15
        col = caja("barra", (x, c[1], 0), (0.75, 0.75, 1.0), mat_metal("barra", (0.14, 0.16, 0.2, 1), 0.2))
        tope_m = mat_estado("tope_%d" % i, CIAN, ROJO, 10.0)
        key_val(tope_m, "estado", [(0, i / max(1, n - 1))])
        tope = caja("tope", (x, c[1], 0.02), (0.78, 0.78, 0.04), tope_m)
        cols.append((col, tope, h))
        col.scale.z = 0.001
        tope.location.z = 0.02
        col.keyframe_insert("scale", frame=0)
        tope.keyframe_insert("location", frame=0)
    k = 0
    for beat, ef in efectos("barras"):
        if ef["tipo"] != "crecer" or k >= n:
            continue
        col, tope, h = cols[k]
        f0 = cuadro(beat, ef.get("palabra"), ef.get("n", 1), desfase=ef.get("desfase", 0))
        col.keyframe_insert("scale", frame=f0 - 1)
        tope.keyframe_insert("location", frame=f0 - 1)
        col.scale.z, col.location.z = h, h / 2
        tope.location.z = h + 0.02
        col.keyframe_insert("scale", frame=f0 + 12)
        col.keyframe_insert("location", frame=f0 + 12)
        tope.keyframe_insert("location", frame=f0 + 12)
        k += 1


# línea de tiempo: marcas que se encienden una por una
def construir_linea(c):
    marcas = DATOS.get("linea", {}).get("marcas") or ["", "", ""]
    n = len(marcas)
    caja("riel", (c[0], c[1], 0.15), ((n - 1) * 1.6 + 1.2, 0.08, 0.04), mat_emisivo("riel", (0.5, 0.6, 0.8, 1), 1.5)[0])
    mats = []
    for i in range(n):
        x = c[0] + (i - (n - 1) / 2) * 1.6
        m = mat_estado("marca_%d" % i, (0.25, 0.3, 0.4, 1), AMBAR, 0.6)
        caja("marca", (x, c[1], 0.9), (0.18, 0.18, 1.5), m)
        mats.append(m)
    k = 0
    for beat, ef in efectos("linea"):
        if ef["tipo"] != "marcar" or k >= n:
            continue
        f0 = cuadro(beat, ef.get("palabra"), ef.get("n", 1), desfase=ef.get("desfase", 0))
        key_val(mats[k], "estado", [(f0 - 1, 0.0), (f0 + 6, 1.0)])
        key_val(mats[k], "brillo", [(f0 - 1, 0.6), (f0 + 4, 16.0), (f0 + 14, 9.0)])
        k += 1


# monolito: pantalla flotante (la capa 2D escribe el contenido encima)
def construir_monolito(c):
    caja("monolito", (c[0], c[1] + 0.2, 3.0), (2.6, 0.25, 4.4), mat_metal("monolito", (0.02, 0.02, 0.025, 1), 0.15))
    m = mat_estado("pantalla", CIAN, ROJO, 2.2)
    caja("pantalla", (c[0], c[1] + 0.06, 3.0), (2.35, 0.02, 4.1), m)
    for beat, ef in efectos("monolito"):
        f0 = cuadro(beat, ef.get("palabra"), ef.get("n", 1), desfase=ef.get("desfase", 0))
        if ef["tipo"] == "alerta":
            key_val(m, "estado", [(f0 - 1, 0.0), (f0 + 4, 1.0)])
            key_val(m, "brillo", [(f0 - 1, 2.2), (f0 + 3, 6.0), (f0 + 12, 3.0)])
        elif ef["tipo"] == "calma":
            key_val(m, "estado", [(f0 - 1, 1.0), (f0 + 10, 0.0)])


# ── espacio (22-oct-2026, aprobada por operador desde la muestra laboratorio/muestra_mars3d_20261022): planeta + nave +
# trayectorias de aproximación. Va LEJOS del set (fuera del piso de 400 u, que llega a x=±200) con su propio domo de
# estrellas, su luz y su CÁMARA: así las demás estaciones (Knight, Cloudflare…) se renderizan igual que antes.
# guion.datos.espacio = {"trayectorias": [{"id": "plan", "km": 226, "color": "cian"}, …], "minimo_km": 80}
# Las alturas guardan su proporción entre sí; contra el planeta están exageradas a propósito (la capa lo dice).
# efectos (anclados a palabras): trayectoria{id} · zona_minima · propulsor · perder_senal · vector{color, largo} · ocultar_nave
# beat.nave = {"trayectoria": id, "desde": grados, "hasta": grados} → la nave la recorre durante ese beat
# beat.toma = "lejos" | "nave" | "cerca" (por omisión) → encuadre de la cámara del espacio
CENTRO["espacio"] = (600.0, 0.0, 300.0)
COLORES = {"cian": CIAN, "ambar": AMBAR, "rojo": ROJO, "verde": VERDE}
ESPACIO = {}


def nodos_mat(nombre):
    m = bpy.data.materials.new(nombre)
    nt = m.node_tree
    nt.nodes.clear()
    return m, nt, nt.nodes.new("ShaderNodeOutputMaterial")


def construir_espacio(c):
    cv = (c[0], c[1], c[2])
    cfg = DATOS.get("espacio", {})
    R = 4.0
    tray = cfg.get("trayectorias", [])
    alturas = [t["km"] for t in tray] + ([cfg["minimo_km"]] if cfg.get("minimo_km") else [])
    K = 0.3 * R / max(alturas or [1])  # la mayor altura queda a 0.3 R
    # domo de estrellas (emisión pura; no ilumina ni da sombra)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=90, location=cv, segments=64, ring_count=32)
    domo = bpy.context.active_object
    m, nt, out = nodos_mat("estrellas")
    tc = nt.nodes.new("ShaderNodeTexCoord")
    nrm = nt.nodes.new("ShaderNodeVectorMath")
    nrm.operation = "NORMALIZE"
    vor = nt.nodes.new("ShaderNodeTexVoronoi")
    vor.inputs["Scale"].default_value = 160
    rmp = nt.nodes.new("ShaderNodeValToRGB")
    rmp.color_ramp.elements[0].position, rmp.color_ramp.elements[0].color = 0.0, (1, 1, 1, 1)
    rmp.color_ramp.elements[1].position, rmp.color_ramp.elements[1].color = 0.05, (0, 0, 0, 1)
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Strength"].default_value = 2.5
    for a, b in [(tc.outputs["Object"], nrm.inputs[0]), (nrm.outputs[0], vor.inputs["Vector"]),
                 (vor.outputs["Distance"], rmp.inputs["Fac"]), (rmp.outputs["Color"], em.inputs["Color"]),
                 (em.outputs[0], out.inputs[0])]:
        nt.links.new(a, b)
    domo.data.materials.append(m)
    domo.visible_shadow = domo.visible_diffuse = domo.visible_glossy = False
    # planeta: color y cráteres procedurales (sin imágenes descargadas)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=R, location=cv, segments=128, ring_count=64)
    planeta = bpy.context.active_object
    bpy.ops.object.shade_smooth()
    planeta.rotation_euler = (math.radians(20), 0, 0)
    m = bpy.data.materials.new("planeta")
    nt = m.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 0.95
    coord = nt.nodes.new("ShaderNodeTexCoord")
    ruido = nt.nodes.new("ShaderNodeTexNoise")
    ruido.inputs["Scale"].default_value = 1.1
    ruido.inputs["Detail"].default_value = 12
    ruido.inputs["Roughness"].default_value = 0.62
    col = nt.nodes.new("ShaderNodeValToRGB")
    el = col.color_ramp.elements
    el[0].position, el[0].color = 0.38, (0.09, 0.03, 0.015, 1)
    el[1].position, el[1].color = 0.62, (0.72, 0.30, 0.11, 1)
    el.new(0.5).color = (0.36, 0.12, 0.045, 1)
    crat = nt.nodes.new("ShaderNodeTexVoronoi")
    crat.inputs["Scale"].default_value = 14
    rc = nt.nodes.new("ShaderNodeValToRGB")
    rc.color_ramp.elements[0].position, rc.color_ramp.elements[0].color = 0.0, (0, 0, 0, 1)
    rc.color_ramp.elements[1].position, rc.color_ramp.elements[1].color = 0.22, (1, 1, 1, 1)
    mul = nt.nodes.new("ShaderNodeMath")
    mul.operation = "MULTIPLY"
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.9
    for a, b in [(coord.outputs["Object"], ruido.inputs["Vector"]), (coord.outputs["Object"], crat.inputs["Vector"]),
                 (ruido.outputs["Fac"], col.inputs["Fac"]), (col.outputs["Color"], bsdf.inputs["Base Color"]),
                 (crat.outputs["Distance"], rc.inputs["Fac"]), (rc.outputs["Color"], mul.inputs[0]),
                 (ruido.outputs["Fac"], mul.inputs[1]), (mul.outputs[0], bump.inputs["Height"]),
                 (bump.outputs["Normal"], bsdf.inputs["Normal"])]:
        nt.links.new(a, b)
    planeta.data.materials.append(m)

    def cascaron(nombre, radio, color, fuerza, mezcla):
        bpy.ops.mesh.primitive_uv_sphere_add(radius=radio, location=cv, segments=128, ring_count=64)
        o = bpy.context.active_object
        bpy.ops.object.shade_smooth()
        mt, nt, out = nodos_mat(nombre)
        lw = nt.nodes.new("ShaderNodeLayerWeight")
        lw.inputs["Blend"].default_value = mezcla
        e = nt.nodes.new("ShaderNodeEmission")
        e.inputs["Color"].default_value = color
        e.inputs["Strength"].default_value = fuerza
        tr = nt.nodes.new("ShaderNodeBsdfTransparent")
        inten = nt.nodes.new("ShaderNodeValue")
        inten.name = "estado"
        inten.outputs[0].default_value = 1.0
        mm = nt.nodes.new("ShaderNodeMath")
        mm.operation = "MULTIPLY"
        mix = nt.nodes.new("ShaderNodeMixShader")
        for a, b in [(lw.outputs["Facing"], mm.inputs[0]), (inten.outputs[0], mm.inputs[1]),
                     (mm.outputs[0], mix.inputs["Fac"]), (tr.outputs[0], mix.inputs[1]), (e.outputs[0], mix.inputs[2]),
                     (mix.outputs[0], out.inputs[0])]:
            nt.links.new(a, b)
        o.data.materials.append(mt)
        o.visible_shadow = False
        return mt

    cascaron("atmosfera", R * 1.01, (1.0, 0.5, 0.3, 1), 1.2, 0.08)
    E = 1.35  # excentricidad de la aproximación (forma visual de la curva)

    def punto(h, th):
        p = R + h
        r = p * (1 + E) / (1 + E * math.cos(th))
        return Vector((cv[0] + r * math.sin(th), cv[1], cv[2] + r * math.cos(th)))

    def curva(nombre, puntos, color):
        cu = bpy.data.curves.new(nombre, "CURVE")
        cu.dimensions = "3D"
        cu.bevel_depth = 0.035
        sp = cu.splines.new("POLY")
        sp.points.add(len(puntos) - 1)
        for pt, v in zip(sp.points, puntos):
            pt.co = (v.x, v.y, v.z, 1)
        o = bpy.data.objects.new(nombre, cu)
        sc.collection.objects.link(o)
        cu.materials.append(mat_emisivo(nombre, color, 6.0)[0])
        o.visible_shadow = False
        cu.bevel_factor_end = 0.0
        cu.keyframe_insert("bevel_factor_end", frame=0)
        return cu

    ths = [math.radians(-120 + i * 0.5) for i in range(481)]
    curvas = {t["id"]: (curva("tray_" + t["id"], [punto(t["km"] * K, th) for th in ths],
                              COLORES.get(t.get("color"), CIAN)), t["km"] * K) for t in tray}
    zona = anillo = None
    if cfg.get("minimo_km"):
        hm = cfg["minimo_km"] * K
        zona = cascaron("zona_minima", R + hm, (1.0, 0.08, 0.05, 1), 2.0, 0.04)
        key_val(zona, "estado", [(0, 0.0)])
        anillo = curva("anillo_minimo", [Vector((cv[0] + (R + hm) * math.sin(th), cv[1], cv[2] + (R + hm) * math.cos(th)))
                                         for th in ths], (1.0, 0.06, 0.04, 1))
    # nave (no a escala)
    oro = mat_metal("oro", (1.0, 0.68, 0.22, 1), 0.28)
    panel = mat_metal("panel_solar", (0.08, 0.22, 0.75, 1), 0.2)
    panel.node_tree.nodes["Principled BSDF"].inputs["Emission Color"].default_value = (0.1, 0.3, 1.0, 1)
    panel.node_tree.nodes["Principled BSDF"].inputs["Emission Strength"].default_value = 0.6
    blanco = mat_metal("blanco", (0.85, 0.85, 0.85, 1), 0.4)
    nave = bpy.data.objects.new("nave", None)
    sc.collection.objects.link(nave)
    partes = [caja("cuerpo", (0, 0, 0), (0.34, 0.30, 0.42), oro), caja("panel", (0.50, 0, -0.05), (0.42, 0.03, 1.15), panel),
              caja("brazo", (0.22, 0, 0.05), (0.22, 0.03, 0.03), blanco)]
    bpy.ops.mesh.primitive_cone_add(radius1=0.22, radius2=0.04, depth=0.12, location=(0, 0, 0.30))
    partes.append(bpy.context.active_object)
    partes[-1].data.materials.append(blanco)
    fuego = mat_estado("fuego", (0.4, 0.75, 1.0, 1), (1.0, 0.85, 0.5, 1), 30.0)
    baliza = mat_estado("baliza", VERDE, ROJO, 8.0)
    for loc, r, mt in (((0, 0, -0.26), 0.07, fuego), ((0, -0.17, 0.12), 0.035, baliza)):
        bpy.ops.mesh.primitive_uv_sphere_add(radius=r, location=loc)
        partes.append(bpy.context.active_object)
        partes[-1].data.materials.append(mt)
    for p in partes:
        p.parent = nave
    nave.scale = (1.05, 1.05, 1.05)
    nave.keyframe_insert("scale", frame=0)

    def flecha(nombre, color, carril):
        """Flecha de fuerza pegada a la nave, a lo largo de su avance; se estira animando scale.z de su raíz.
        «carril» las separa en paralelo para que dos flechas del mismo largo no queden encimadas."""
        raiz = bpy.data.objects.new(nombre, None)
        sc.collection.objects.link(raiz)
        raiz.parent = nave
        raiz.location = (0.22 * carril, -0.25, -0.3)
        raiz.rotation_euler = (math.pi, 0, 0)  # apunta a −Z local de la nave = hacia adelante en la trayectoria
        m = mat_emisivo(nombre, color, 8.0)[0]
        bpy.ops.mesh.primitive_cylinder_add(radius=0.035, depth=0.45, location=(0, 0, 0.225))  # largo 1 = 0.5 u
        tallo = bpy.context.active_object
        bpy.ops.mesh.primitive_cone_add(radius1=0.1, radius2=0.0, depth=0.1, location=(0, 0, 0.5))
        punta = bpy.context.active_object
        for o in (tallo, punta):
            o.data.materials.append(m)
            o.parent = raiz
            o.visible_shadow = False
        raiz.scale = (1, 1, 0.0)
        raiz.keyframe_insert("scale", frame=0)
        return raiz

    flechas = {}
    # efectos
    for beat, ef in efectos("espacio"):
        f0 = cuadro(beat, ef.get("palabra"), ef.get("n", 1), desfase=ef.get("desfase", 0))
        if ef["tipo"] == "trayectoria" and ef.get("id") in curvas:
            cu = curvas[ef["id"]][0]
            cu.bevel_factor_end = 0.0
            cu.keyframe_insert("bevel_factor_end", frame=max(0, f0 - 9))
            cu.bevel_factor_end = 1.0
            cu.keyframe_insert("bevel_factor_end", frame=f0 + round(ef.get("segundos", 2.0) * FPS))
        elif ef["tipo"] == "zona_minima" and zona:
            key_val(zona, "estado", [(f0 - 2, 0.0), (f0 + 10, 1.0)])
            anillo.bevel_factor_end = 0.0
            anillo.keyframe_insert("bevel_factor_end", frame=max(0, f0 - 3))
            anillo.bevel_factor_end = 1.0
            anillo.keyframe_insert("bevel_factor_end", frame=f0 + 27)
        elif ef["tipo"] == "propulsor":
            key_val(fuego, "estado", [(f0 - 1, 0.0), (f0 + 3, 1.0), (f0 + 18, 0.0)])
            key_val(fuego, "brillo", [(f0 - 1, 30.0), (f0 + 3, 140.0), (f0 + 18, 30.0)])
        elif ef["tipo"] == "perder_senal":
            key_val(baliza, "estado", [(f0 - 1, 0.0), (f0 + 2, 1.0)])
            key_val(baliza, "brillo", [(f0 - 1, 8.0), (f0 + 2, 25.0), (f0 + 14, 0.0)])
        elif ef["tipo"] == "vector":
            # {"tipo": "vector", "color": "cian", "largo": 1.0, "palabra": …}: crece en la palabra, se recoge al final del beat
            col = ef.get("color", "cian")
            fl = flechas.get(col) or flechas.setdefault(col, flecha("vector_" + col, COLORES.get(col, CIAN), len(flechas)))
            fb = next(x["f_fin"] for x in TL["beats"] if x["id"] == beat)
            fl.scale = (1, 1, 0.0)
            fl.keyframe_insert("scale", frame=max(0, f0 - 1))
            fl.scale = (1, 1, float(ef.get("largo", 1.0)))
            fl.keyframe_insert("scale", frame=f0 + 15)
            fl.keyframe_insert("scale", frame=fb + 4)
            fl.scale = (1, 1, 0.0)
            fl.keyframe_insert("scale", frame=fb + 12)
        elif ef["tipo"] == "ocultar_nave":
            nave.keyframe_insert("scale", frame=max(0, f0 - 1))
            nave.scale = (0.0, 0.0, 0.0)
            nave.keyframe_insert("scale", frame=f0 + 8)
            nave.scale = (1.05, 1.05, 1.05)
    # recorrido de la nave: solo durante los beats del espacio que lo piden
    primero = True
    for b in TL["beats"]:
        gb = GB[b["id"]]
        nv = gb.get("nave")
        if gb.get("estacion") != "espacio" or not nv or nv.get("trayectoria") not in curvas:
            continue
        h = curvas[nv["trayectoria"]][1]
        a0, a1 = math.radians(nv.get("desde", -50)), math.radians(nv.get("hasta", -10))
        for fr in range(0 if primero else b["f_ini"], b["f_fin"] + 1):
            x = max(0.0, min(1.0, (fr - b["f_ini"]) / max(1, b["f_fin"] - b["f_ini"])))
            th = a0 + (a1 - a0) * (1 - (1 - x) ** 1.6)
            pos = punto(h, th)
            tan = (punto(h, th + 0.01) - pos).normalized()
            nave.location = pos + Vector((0, -0.35, 0))
            nave.rotation_euler = Vector((0, 0, 1)).rotation_difference(-tan).to_euler()
            nave.keyframe_insert("location", frame=fr)
            nave.keyframe_insert("rotation_euler", frame=fr)
        primero = False
    # luz propia: un área grande al lado (un sol iluminaría también las otras estaciones)
    d = Vector((0.95, -0.15, -0.25)).normalized()
    ld = bpy.data.lights.new("luz_espacio", "AREA")
    ld.energy, ld.size = float(os.environ.get("LUZ_ESPACIO", 60000)), 25
    lo = bpy.data.objects.new("luz_espacio", ld)
    lo.location = Vector(cv) - d * 45
    lo.rotation_euler = Vector((0, 0, -1)).rotation_difference(d).to_euler()
    sc.collection.objects.link(lo)
    # cámara propia (la del pivote orbita a 10 u con otro lente y no encuadra el planeta)
    cde = bpy.data.cameras.new("cam_espacio")
    cde.lens, cde.sensor_fit, cde.shift_y = 30, "VERTICAL", 0.16
    ce = bpy.data.objects.new("cam_espacio", cde)
    sc.collection.objects.link(ce)
    mira = bpy.data.objects.new("mira_espacio", None)
    sc.collection.objects.link(mira)
    ce.constraints.new("TRACK_TO").target = mira
    TOMAS = {"cerca": (((-3.2, -10.5, 6.6), (-1.9, 0, 3.6)), ((-0.9, -8.8, 6.0), (-0.9, 0, 3.7))),
             "lejos": (((-7.0, -19.0, 8.5), (-1.5, 0, 2.5)), ((-4.5, -16.0, 7.5), (-1.2, 0, 2.8))),
             # centrada en la nave y más abajo en el cuadro: la tarjeta de arriba no la tapa (flechas de fuerza)
             "nave": (((-1.4, -12.5, 6.4), (-1.0, 0, 4.9)), ((-0.9, -11.5, 6.2), (-0.7, 0, 4.9)))}
    # beats del espacio seguidos con la misma toma = UN movimiento continuo; entre tomas distintas, corte en la pausa
    tramos = []
    for b in TL["beats"]:
        gb = GB[b["id"]]
        if gb.get("estacion") != "espacio":
            tramos.append(None)
            continue
        toma = gb.get("toma", "cerca")
        if tramos and tramos[-1] and tramos[-1][0] == toma:
            tramos[-1][2] = b["f_fin"]
        else:
            tramos.append([toma, b["f_ini"], b["f_fin"]])
    tramos = [t for t in tramos if t]
    # la pausa entre frases es de ~13 cuadros: fin de una toma en f1+6 e inicio de la siguiente en f0-6 quedan a ≤1 cuadro,
    # así que el cambio de toma es un corte, no un barrido
    for toma, f0, f1 in tramos:
        for fr, (cpos, mpos) in zip((f0 - 6, f1 + 6), TOMAS[toma]):
            ce.location = Vector(cv) + Vector(cpos)
            mira.location = Vector(cv) + Vector(mpos)
            ce.keyframe_insert("location", frame=max(0, fr))
            mira.keyframe_insert("location", frame=max(0, fr))
    ESPACIO["cam"] = ce


CONSTRUIR = {"racks": construir_racks, "globo": construir_globo, "barras": construir_barras,
             "linea": construir_linea, "monolito": construir_monolito, "espacio": construir_espacio}
usadas = {b.get("estacion") for b in G["beats"] if b.get("estacion")}
for e in ESTACIONES + ["espacio"]:
    if e in usadas:
        piv = bpy.data.objects.new("estacion_" + e, None)
        sc.collection.objects.link(piv)
        CONSTRUIR[e](CENTRO[e])

# ── piso liso reflejante (SIN rejilla) + mundo con halo ─────────────────────
bpy.ops.mesh.primitive_plane_add(size=400, location=(0, 0, 0))
piso = bpy.context.active_object
mp = bpy.data.materials.new("piso")
pp = mp.node_tree.nodes["Principled BSDF"]
pp.inputs["Base Color"].default_value = (0.006, 0.008, 0.014, 1)
pp.inputs["Roughness"].default_value = 0.16
pp.inputs["Metallic"].default_value = 0.4
piso.data.materials.append(mp)
wd = bpy.data.worlds.new("w")
sc.world = wd
wn = wd.node_tree
bg = wn.nodes["Background"]
grad = wn.nodes.new("ShaderNodeTexGradient")
grad.gradient_type = "SPHERICAL"
tcw = wn.nodes.new("ShaderNodeTexCoord")
wn.links.new(tcw.outputs["Generated"], grad.inputs["Vector"])
rmp = wn.nodes.new("ShaderNodeValToRGB")
rmp.color_ramp.elements[0].color = (0.002, 0.003, 0.008, 1)
rmp.color_ramp.elements[1].color = (0.02, 0.035, 0.07, 1)
wn.links.new(grad.outputs["Fac"], rmp.inputs[0])
wn.links.new(rmp.outputs[0], bg.inputs["Color"])

# luces: una suave por estación usada + relleno general
for e in usadas & set(ESTACIONES):
    c = CENTRO[e]
    ld = bpy.data.lights.new("l_" + e, "AREA")
    ld.energy, ld.size, ld.color = 2600, 7, (0.75, 0.85, 1.0)
    lo = bpy.data.objects.new("l_" + e, ld)
    lo.location = (c[0] + 3, -7, 8)
    sc.collection.objects.link(lo)
    lo.constraints.new("TRACK_TO").target = bpy.data.objects["estacion_" + e]
    bpy.data.objects["estacion_" + e].location = (c[0], c[1], 1.5)

# ── cámara: viaja a la estación de cada beat (1 s de vuelo) y orbita lento dentro del beat ──
piv = bpy.data.objects.new("pivote", None)
sc.collection.objects.link(piv)
cd = bpy.data.cameras.new("cam")
cd.lens = 26
cd.shift_x = float(os.environ.get("CAM_SHIFT_X", 0.0))
cd.dof.use_dof = True
cd.dof.focus_object = piv
cd.dof.aperture_fstop = 2.8
cam = bpy.data.objects.new("cam", cd)
cam.parent = piv
cam.location = (0, -10.0, 3.4)
sc.collection.objects.link(cam)
sc.camera = cam
cam.constraints.new("TRACK_TO").target = piv
VUELO = round(1.0 * FPS)
prev, giro = None, -10.0
for b in TL["beats"]:
    e = GB[b["id"]].get("estacion") or prev or "monolito"
    if e == "espacio":   # lo filma su propia cámara; el pivote se queda (o viaja oculto) hacia la siguiente estación
        continue
    c = CENTRO[e]
    z = 3.0 if e in ("globo", "monolito") else 1.6
    if prev is None:                      # primer beat: ya está ahí desde el cuadro 0
        piv.location = (c[0], c[1], z)
        piv.rotation_euler = (0, 0, math.radians(giro))
        piv.keyframe_insert("location", frame=0)
        piv.keyframe_insert("rotation_euler", frame=0)
    elif e != prev:                       # vuelo de 1 s: se SOSTIENE la posición previa hasta despegar
        fi = max(1, b["f_ini"] - VUELO // 2)
        piv.keyframe_insert("location", frame=fi)
        piv.keyframe_insert("rotation_euler", frame=fi)
        giro = -10.0
        piv.location = (c[0], c[1], z)
        piv.rotation_euler = (0, 0, math.radians(giro))
        piv.keyframe_insert("location", frame=fi + VUELO)
        piv.keyframe_insert("rotation_euler", frame=fi + VUELO)
    giro = min(giro + 12.0, 25.0)         # órbita lenta dentro del beat
    piv.rotation_euler = (0, 0, math.radians(giro))
    piv.keyframe_insert("rotation_euler", frame=b["f_fin"])
    prev = e
# cortes de cámara (solo si hay espacio): un marcador por cambio, en la pausa antes del beat
if ESPACIO:
    actual = None
    for b in TL["beats"]:
        cam_b = ESPACIO["cam"] if GB[b["id"]].get("estacion") == "espacio" else cam
        if cam_b is not actual:
            mk = sc.timeline_markers.new("cam_" + b["id"], frame=0 if actual is None else max(0, b["f_ini"] - 8))
            mk.camera = cam_b
            actual = cam_b
    sc.camera = (ESPACIO["cam"] if GB[TL["beats"][0]["id"]].get("estacion") == "espacio" else cam)
LENTE = float(os.environ.get("LENTE", 1.0))   # <1 abre el campo (en 16:9 el campo vertical es más estrecho)
for fr, lens in ((0, 30), (NF // 2, 34), (NF - 1, 31)):
    cd.lens = lens * LENTE
    cd.keyframe_insert("lens", frame=fr)

# bloom
sc.compositing_node_group = bpy.data.node_groups.new("comp", "CompositorNodeTree")
cn = sc.compositing_node_group
rl = cn.nodes.new("CompositorNodeRLayers")
gl = cn.nodes.new("CompositorNodeGlare")
gl.inputs["Type"].default_value = "Bloom"
gl.inputs["Threshold"].default_value = 0.8
gl.inputs["Strength"].default_value = 0.5
og = cn.nodes.new("NodeGroupOutput")
cn.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
cn.links.new(rl.outputs["Image"], gl.inputs["Image"])
cn.links.new(gl.outputs["Image"], og.inputs[0])

sc.render.image_settings.file_format = "PNG"
sc.render.image_settings.compression = 10
if os.environ.get("CUADRO"):
    for fr in os.environ["CUADRO"].split(","):
        sc.frame_set(int(fr))
        print("DIAG", fr, tuple(round(v, 1) for v in piv.matrix_world.translation),
              tuple(round(v, 1) for v in cam.matrix_world.translation), flush=True)
        sc.render.filepath = str(D / "prueba" / ("cuadro_%04d.png" % int(fr)))
        bpy.ops.render.render(write_still=True)
    sys.exit(0)
sc.render.filepath = str(D / "cuadros3d" / "f_")
if os.environ.get("RANGO"):       # p. ej. solo el tramo 3D de una muestra
    sc.frame_start, sc.frame_end = (int(x) for x in os.environ["RANGO"].split("-"))
sc.render.use_overwrite = False   # reanudable: salta cuadros ya hechos
sc.render.use_placeholder = True
# El «placeholder» es un PNG vacío mientras se dibuja el cuadro: si el proceso muere (22-oct: falta de memoria), ese
# vacío se daba por hecho al reanudar. Se aparta (renombrado, no borrado) para que se vuelva a dibujar.
for vacio in (D / "cuadros3d").glob("f_*.png") if (D / "cuadros3d").is_dir() else []:
    if vacio.stat().st_size == 0:
        vacio.rename(vacio.with_name(vacio.name + ".vacio"))
        print("cuadro vacío apartado:", vacio.name, flush=True)
bpy.ops.render.render(animation=True)
print("LISTO", NF, "cuadros")
