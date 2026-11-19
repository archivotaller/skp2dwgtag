"""Fragmentos Ruby que se ejecutan dentro de SketchUp vía eval_ruby.

Todo lo que devuelve datos lo hace como JSON (a un archivo o como texto),
para no depender del formato ``inspect`` de Ruby.
"""

from __future__ import annotations

import json
from typing import Any

RENDERING_KEYS = [
    "DisplayColorByLayer",
    "EdgeColorMode",
    "RenderMode",
    "DrawSilhouettes",
    "DrawProfiles",
    "DrawDepthQue",
    "DisplayTextures",
    "DisplayShadows",
    "DisplayFog",
    "DrawHiddenGeometry",
    "DrawHiddenObjects",
]


def _q(value: str) -> str:
    """Literal Ruby seguro para una ruta."""
    return json.dumps(value)


def model_info() -> str:
    return r"""
require 'json'
m = Sketchup.active_model
def skp2dwg_sections(ents, out)
  ents.each do |e|
    if e.is_a?(Sketchup::SectionPlane)
      out << { name: (e.name.to_s rescue ''), active: (e.active? rescue false), plane: (e.get_plane rescue nil) }
    elsif e.is_a?(Sketchup::Group) || e.is_a?(Sketchup::ComponentInstance)
      begin
        skp2dwg_sections(e.definition.entities, out)
      rescue
      end
    end
  end
end
secs = []
skp2dwg_sections(m.entities, secs)
bb = m.bounds
JSON.generate({
  title: m.title,
  path: m.path,
  units: m.options['UnitsOptions']['LengthUnit'],
  layers: m.layers.map { |l| { name: l.name, visible: l.visible?, rgb: [l.color.red, l.color.green, l.color.blue] } },
  page_count: m.pages.size,
  pages: m.pages.map { |p| p.name },
  section_planes: secs,
  bounds: { min: bb.min.to_a, max: bb.max.to_a },
  entity_count: m.entities.size,
  style: (m.styles.selected_style.name rescue nil)
})
"""


def snapshot_state(path: str) -> str:
    return f"""
require 'json'
m = Sketchup.active_model
cam = m.active_view.camera
rendering = {{}}
{json.dumps(RENDERING_KEYS)}.each do |k|
  begin; rendering[k] = m.rendering_options[k]; rescue; end
end
data = {{
  layers: m.layers.map {{ |l| {{ name: l.name, visible: l.visible?, rgb: [l.color.red, l.color.green, l.color.blue] }} }},
  active_layer: m.active_layer.name,
  page: (m.pages.selected_page && m.pages.selected_page.name),
  page_options: {{ show_transition: (m.options['PageOptions']['ShowTransition'] rescue nil) }},
  camera: {{
    eye: cam.eye.to_a, target: cam.target.to_a, up: cam.up.to_a,
    perspective: cam.perspective?, height: cam.height, fov: cam.fov
  }},
  sections: m.entities.grep(Sketchup::SectionPlane).map {{ |s| {{ name: s.name.to_s, active: s.active? }} }},
  rendering: rendering
}}
File.write({_q(path)}, JSON.generate(data))
JSON.generate({{ ok: true }})
"""


def restore_state(path: str) -> str:
    return f"""
require 'json'
m = Sketchup.active_model
data = JSON.parse(File.read({_q(path)}))
begin
  if data['page']
    page = m.pages[data['page']]
    m.pages.selected_page = page if page
  end
rescue
end
view = m.active_view
cam = Sketchup::Camera.new(
  Geom::Point3d.new(*data['camera']['eye']),
  Geom::Point3d.new(*data['camera']['target']),
  Geom::Vector3d.new(*data['camera']['up'])
)
cam.perspective = data['camera']['perspective']
begin
  if cam.perspective
    cam.fov = data['camera']['fov'] if data['camera']['fov']
  else
    cam.height = data['camera']['height'] if data['camera']['height']
  end
rescue
end
view.camera = cam
(data['layers'] || []).each do |l|
  begin
    layer = m.layers[l['name']]
    if layer
      layer.visible = l['visible']
      if l['rgb']
        layer.color = Sketchup::Color.new(l['rgb'][0], l['rgb'][1], l['rgb'][2])
      end
    end
  rescue
  end
end
begin
  if m.layers[data['active_layer']]
    m.active_layer = m.layers[data['active_layer']]
  end
rescue
end
secs = {{}}
m.entities.grep(Sketchup::SectionPlane).each {{ |s| secs[s.name.to_s] = s.active? }}
active = (data['sections'] || []).find {{ |x| x['active'] }}
target = nil
if active
  target = m.entities.grep(Sketchup::SectionPlane).find {{ |x| x.name.to_s == active['name'] }}
end
begin
  m.entities.active_section_plane = target
rescue
end
(data['rendering'] || {{}}).each do |k, v|
  begin; m.rendering_options[k] = v; rescue; end
end
if data['page_options']
  begin
    if data['page_options'].key?('show_transition')
      m.options['PageOptions']['ShowTransition'] = data['page_options']['show_transition']
    end
  rescue
  end
end
JSON.generate({{ ok: true }})
"""


def set_view(
    eye: list[float],
    target: list[float],
    up: list[float],
    ortho: bool = True,
    section: str | None = None,
    all_layers_visible: bool = True,
    visible_layers: list[str] | None = None,
    zoom_extents: bool = True,
) -> str:
    section_ruby = "nil" if section is None else _q(section)
    visible_ruby = (
        "nil" if visible_layers is None else json.dumps(visible_layers, ensure_ascii=False)
    )
    return f"""
require 'json'
m = Sketchup.active_model
view = m.active_view
cam = Sketchup::Camera.new(Geom::Point3d.new(*{json.dumps(eye)}),
                           Geom::Point3d.new(*{json.dumps(target)}),
                           Geom::Vector3d.new(*{json.dumps(up)}))
cam.perspective = {str(not ortho).lower()}
view.camera = cam
{'view.zoom_extents' if zoom_extents else ''}
section_name = {section_ruby}
if section_name
  planes = m.entities.grep(Sketchup::SectionPlane)
  target = planes.find {{ |sp| sp.name.to_s == section_name }}
  begin
    m.entities.active_section_plane = target
  rescue
    target.activate if target
  end
end
visible_set = {visible_ruby}
m.layers.each do |l|
  if visible_set
    l.visible = visible_set.include?(l.name)
  elsif {str(bool(all_layers_visible)).lower()}
    l.visible = true
  end
end
cam = view.camera
JSON.generate({{
  eye: cam.eye.to_a, target: cam.target.to_a, up: cam.up.to_a,
  perspective: cam.perspective?, height: cam.height, fov: cam.fov
}})
"""


def export_2d(dxf_path: str) -> str:
    return f"""
require 'json'
m = Sketchup.active_model
path = {_q(dxf_path)}
ok = m.export(path, {{ acad_version: 'acad_2018', show_summary: false }})
JSON.generate({{ ok: ok, exists: File.exist?(path), size: (File.size(path) rescue nil) }})
"""


def activate_scene(name: str, all_layers_visible: bool = True) -> str:
    return f"""
require 'json'
m = Sketchup.active_model
page = m.pages.find {{ |p| p.name == {_q(name)} }}
m.pages.selected_page = page if page
begin
  if page && page.use_camera? && page.camera
    pc = page.camera
    cam = Sketchup::Camera.new(pc.eye, pc.target, pc.up)
    cam.perspective = false
    m.active_view.camera = cam
  end
rescue
end
if {str(bool(all_layers_visible)).lower()}
  m.layers.each {{ |l| l.visible = true }}
end
active = nil
begin
  sp = m.entities.active_section_plane
  active = sp.name.to_s if sp
rescue
end
c = m.active_view.camera
JSON.generate({{ ok: !!page, section: active, perspective: c.perspective?,
  eye: c.eye.to_a, target: c.target.to_a, up: c.up.to_a }})
"""


def set_page_transitions(enabled: bool) -> str:
    return f"""
require 'json'
m = Sketchup.active_model
begin
  m.options['PageOptions']['ShowTransition'] = {str(bool(enabled)).lower()}
rescue
end
JSON.generate({{ ok: true }})
"""


def section_cut(plane_name: str, path: str) -> str:
    return f"""
require 'json'
m = Sketchup.active_model
name = {_q(plane_name)}
sp = m.entities.grep(Sketchup::SectionPlane).find {{ |s| s.name.to_s == name }}
raise "No existe el plano de sección '#{{name}}'" unless sp
plane = sp.get_plane
a, b, c, d = plane
n = Geom::Vector3d.new(a, b, c)
len2 = a*a + b*b + c*c
p0 = Geom::Point3d.new(-d*a/len2, -d*b/len2, -d*c/len2)

def skp2dwg_cut_tri(p1, p2, p3, p0, n, eps)
  pts = []
  [[p1,p2],[p2,p3],[p3,p1]].each do |u, v|
    du = (u - p0).dot(n)
    dv = (v - p0).dot(n)
    next if du.abs < eps && dv.abs < eps
    if (du <= 0 && dv >= 0) || (du >= 0 && dv <= 0)
      den = du - dv
      next if den.abs < eps
      t = du / den
      pts << Geom::Point3d.new(u.x + (v.x-u.x)*t, u.y + (v.y-u.y)*t, u.z + (v.z-u.z)*t)
    end
  end
  uniq = []
  pts.each do |p|
    next if uniq.any? {{ |q| q.distance(p) < eps }}
    uniq << p
  end
  uniq
end

segs = []
walk = lambda do |ents, tr|
  ents.each do |e|
    if e.is_a?(Sketchup::Face)
      begin
        mesh = e.mesh(0)
        mesh.polygons.each do |poly|
          ps = poly.map {{ |i| mesh.point_at(i).transform(tr) }}
          next unless ps.size == 3
          q = skp2dwg_cut_tri(ps[0], ps[1], ps[2], p0, n, 1e-6)
          if q.size == 2
            segs << [q[0].x.to_f, q[0].y.to_f, q[0].z.to_f, q[1].x.to_f, q[1].y.to_f, q[1].z.to_f]
          end
        end
      rescue
      end
    elsif e.is_a?(Sketchup::Group) || e.is_a?(Sketchup::ComponentInstance)
      walk.call(e.definition.entities, tr * e.transformation)
    end
  end
end
walk.call(m.entities, Geom::Transformation.new)

seen = {{}}
uniq = []
segs.each do |s|
  key = s.map {{ |v| (v * 10000).round }}.sort.join(',')
  next if seen[key]
  seen[key] = true
  uniq << s
end
File.write({_q(path)}, JSON.generate({{ plane: plane, segments: uniq }}))
JSON.generate({{ segments: uniq.size, plane: plane }})
"""


def extract_edges(path: str) -> str:
    return f"""
require 'json'
m = Sketchup.active_model
edges = []
walk = lambda do |ents, tr, tag, vis|
  ents.each do |e|
    v = vis
    begin; v = v && e.visible?; rescue; end
    if e.is_a?(Sketchup::Edge)
      t = e.layer.name
      t = tag if t == 'Layer0' && tag
      a = e.start.position.transform(tr)
      b = e.end.position.transform(tr)
      edges << [a.x.to_f, a.y.to_f, a.z.to_f, b.x.to_f, b.y.to_f, b.z.to_f, t, (v ? 1 : 0)]
    elsif e.is_a?(Sketchup::Group) || e.is_a?(Sketchup::ComponentInstance)
      lt = e.layer.name
      nt = (lt == 'Layer0') ? tag : lt
      walk.call(e.definition.entities, tr * e.transformation, nt, v)
    end
  end
end
walk.call(m.entities, Geom::Transformation.new, nil, true)
cam = m.active_view.camera
bb = m.bounds
data = {{
  camera: {{ eye: cam.eye.to_a, target: cam.target.to_a, up: cam.up.to_a, perspective: cam.perspective?, height: cam.height, fov: cam.fov }},
  units: m.options['UnitsOptions']['LengthUnit'],
  bounds: {{ min: bb.min.to_a, max: bb.max.to_a }},
  edges: edges
}}
File.write({_q(path)}, JSON.generate(data))
JSON.generate({{ edges: edges.size, size: (File.size(path) rescue nil) }})
"""


def set_layer_visibility(visibility: dict[str, bool]) -> str:
    payload = json.dumps(visibility, ensure_ascii=False)
    return f"""
require 'json'
m = Sketchup.active_model
vis = {payload}
vis.each do |name, value|
  begin
    layer = m.layers[name]
    layer.visible = value if layer
  rescue
  end
end
JSON.generate({{ ok: true }})
"""


def activate_section(name: str | None) -> str:
    section_ruby = "nil" if name is None else _q(name)
    return f"""
require 'json'
m = Sketchup.active_model
name = {section_ruby}
planes = m.entities.grep(Sketchup::SectionPlane) + m.definitions.flat_map {{ |d| d.entities.grep(Sketchup::SectionPlane) }}
planes.each {{ |sp| sp.active = (name && sp.name.to_s == name) }}
JSON.generate({{ active: planes.select(&:active?).map {{ |s| s.name.to_s }} }})
"""


def layer_state() -> str:
    return r"""
require 'json'
m = Sketchup.active_model
JSON.generate(m.layers.map { |l| { name: l.name, visible: l.visible?, rgb: [l.color.red, l.color.green, l.color.blue] } })
"""


def units_name(units: Any) -> str:
    return {
        0: "in",
        1: "ft",
        2: "mm",
        3: "cm",
        4: "m",
        5: "yd",
    }.get(units, "mm")
