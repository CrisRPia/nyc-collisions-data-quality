"""Interactive borough boundaries and sampled crash locations for the notebook."""

from collections.abc import Sequence
from html import escape
import json

import folium
from IPython.display import HTML

from analysis.data_types import BoroughBoundaries
from analysis.domain import geographic_domain


def borough_map(
    geometry: BoroughBoundaries, coordinates: Sequence[tuple[float, float]],
    *, state: str, omitted: int,
) -> HTML:
    """Hover labels come from official polygons; points leave their hover area clear."""
    domain = geographic_domain(geometry)
    map_view = folium.Map(tiles=None, prefer_canvas=True, control_scale=True)
    map_view.fit_bounds([[domain.south, domain.west], [domain.north, domain.east]])

    boundaries = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", **feature} for feature in geometry["features"]],
    }
    _ = folium.GeoJson(
        json.dumps(boundaries),
        name="Distritos",
        style_function=lambda _feature: {
            "color": "#607985", "weight": 1, "fillColor": "#cde7df", "fillOpacity": 0.3,
        },
        highlight_function=lambda _feature: {"weight": 2, "fillOpacity": 0.6},
        tooltip=folium.GeoJsonTooltip(fields=["boroname"], aliases=["Distrito:"], sticky=True),
    ).add_to(map_view)

    points = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {},
             "geometry": {"type": "Point", "coordinates": [longitude, latitude]}}
            for latitude, longitude in coordinates
        ],
    }
    if coordinates:
        point_marker = folium.CircleMarker(
            radius=2, stroke=False, fill=True, fill_color="#16887b", fill_opacity=0.3,
        )
        point_marker.options["interactive"] = False
        _ = folium.GeoJson(
            json.dumps(points),
            name="Siniestros",
            marker=point_marker,
        ).add_to(map_view)

    document = map_view.get_root().render()
    caption = f"{state} · {len(coordinates):,} ubicaciones muestreadas · {omitted:,} registros fuera del mapa"
    return HTML(f'''<p>{escape(caption)}</p>
<iframe title="Mapa de siniestros: pasar el cursor para identificar el distrito"
width="100%" height="640" style="border:0"
srcdoc="{escape(document, quote=True)}"></iframe>''')
