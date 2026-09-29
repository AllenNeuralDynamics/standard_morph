"""Build Neuroglancer viewer states and links for the written layers.

States are plain JSON dicts, so building a link does not need the
``neuroglancer`` package. Sources are URLs Neuroglancer can fetch from a
browser, e.g. ``precomputed://s3://bucket/prefix/skeletons``.
"""
import json
import urllib.parse

from standard_morph.neuroglancer.annotations import RELATIONSHIP
from standard_morph.neuroglancer.coordinates import dimensions_json

DEFAULT_BASE_URL = "https://neuroglancer-demo.appspot.com"
SKELETON_LAYER = "neurons"
IMAGE_LAYER = "image"

#: Draws nodes flagged by any metric in red, and everything else in the
#: segment's own color.
DEFAULT_SKELETON_SHADER = """void main() {
  if (n_flags > 0.5) {
    emitRGB(vec3(1.0, 0.0, 0.0));
  } else {
    emitDefault();
  }
}
"""

ANNOTATION_SHADER = """void main() {
  setColor(defaultColor());
  setPointMarkerSize(10.0);
}
"""

#: Colors cycled over the annotation layers (one per metric).
ANNOTATION_COLORS = (
    "#ffff00", "#00ffff", "#ff00ff", "#ff8000", "#00ff80",
    "#8080ff", "#ff0080", "#80ff00", "#0080ff", "#ffffff",
)

# Characters left unescaped in the URL fragment, as neuroglancer.to_url does.
_URL_SAFE = "~@#$&()*!+=:;,.?/'"


def build_state(skeleton_source, annotation_sources, scale_um, image_source=None,
                segments=None, position=None, shader=DEFAULT_SKELETON_SHADER):
    """Return a Neuroglancer viewer state (a JSON dict).

    Parameters
    ----------
    skeleton_source : str
        Source URL of the skeleton layer, or None to leave it out.
    annotation_sources : dict[str, str]
        ``{metric: source URL}``, one annotation layer each, in order.
    scale_um : sequence of 3 floats
        Micrometers per SWC unit; sets the viewer's dimensions, so
        ``position`` is in SWC units.
    image_source : str, optional
        Any Neuroglancer image source (e.g. ``zarr://s3://.../fused.zarr``),
        added as the bottom layer.
    segments : list[int], optional
        Segment ids to show in the skeleton layer.
    position : sequence of 3 floats, optional
        Initial position, in SWC units.
    """
    layers = []
    if image_source:
        layers.append({"type": "image", "name": IMAGE_LAYER, "source": image_source})
    if skeleton_source:
        layers.append({
            "type": "segmentation",
            "name": SKELETON_LAYER,
            "source": skeleton_source,
            "segments": [str(s) for s in (segments or [])],
            "skeletonRendering": {"shader": shader, "mode2d": "lines_and_points", "mode3d": "lines"},
        })
    for i, (metric, source) in enumerate(annotation_sources.items()):
        layer = {
            "type": "annotation",
            "name": metric,
            "source": source,
            "tab": "annotations",
            "annotationColor": ANNOTATION_COLORS[i % len(ANNOTATION_COLORS)],
            "shader": ANNOTATION_SHADER,
        }
        if skeleton_source:
            layer["linkedSegmentationLayer"] = {RELATIONSHIP: SKELETON_LAYER}
            layer["filterBySegmentation"] = [RELATIONSHIP]
        layers.append(layer)

    state = {"dimensions": dimensions_json(scale_um), "layers": layers, "layout": "4panel"}
    if position is not None:
        state["position"] = [float(v) for v in position]
    if skeleton_source:
        state["selectedLayer"] = {"layer": SKELETON_LAYER, "visible": True}
    return state


def state_to_url(state, base_url=DEFAULT_BASE_URL):
    """Encode ``state`` as a Neuroglancer link (the same encoding as
    ``neuroglancer.to_url``)."""
    fragment = urllib.parse.quote(json.dumps(state, separators=(",", ":")), safe=_URL_SAFE)
    return f"{base_url.rstrip('/')}/#!{fragment}"
