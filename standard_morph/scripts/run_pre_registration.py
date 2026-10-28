"""Run QC on an SWC in S3 and write ``qc.json`` to an S3 prefix.
 
Edit the constants below, then run ``python scripts/run_qc_s3.py``. Needs the
``data-schema`` extra plus ``s3fs`` and AWS credentials that can read the SWC
and write to the output prefix. The QC is also posted to the metadata portal as
a temporary preview record.
"""
import uuid
from urllib.parse import quote
 
import requests
 
from standard_morph import QCContext, Space, run_qc
from standard_morph.data_schema import QC_FILENAME, to_quality_control
 
 
ASSET_NAME = "exaSPIM_648434_2023-08-29_15-00-36_reconstructions_2026-08-28_23-04-54"
NEURON_ID = "N004-648434"
SWC_PATH = f"s3://aind-open-data/{ASSET_NAME}/specimen_space_reconstructions/swc/{NEURON_ID}.swc"
PRECOMPUTED_PATH = f"s3://aind-open-data/{ASSET_NAME}/specimen_space_reconstructions/reconstructions.precomputed"
IMAGE_ZARR_PATH = "s3://aind-open-data/exaSPIM_648434_2023-08-29_15-00-36_fusion_2023-09-22/fused-denoised.zarr"
 
SPACE = Space.IMAGE_SPACE
SUITE_NAME = "default_pre_registration_tests"
PORTAL_URL = "https://data.allenneuraldynamics-test.org"
# PORTAL_URL = "http://localhost:5173"
TEMPORARY_METADATA_URL = f"{PORTAL_URL}/metadata-viz/temporary-metadata"
QC_VIEW_URL = f"{PORTAL_URL}/quality_control"
RECORD_NAME = f"standard_morph_test_{uuid.uuid4()}"
OUTPUT_PREFIX = f"s3://aind-scratch-data/tmchartrand/qc_test/{RECORD_NAME}"
 
 
def main():
    resources = {"image_zarr_path": IMAGE_ZARR_PATH} if IMAGE_ZARR_PATH else {}
    context = QCContext(space=SPACE, resources=resources)
 
    report = run_qc(SWC_PATH, context, suite_name=SUITE_NAME)

    qc = to_quality_control(report, skeleton=PRECOMPUTED_PATH)
 
    record = {
        "quality_control": qc.model_dump(mode="json"),
        # location is only needed if the precomputed or zarr path need to be relative
        # "location": OUTPUT_PREFIX, 
        "name": RECORD_NAME,
    }
    response = requests.post(TEMPORARY_METADATA_URL, json=record, timeout=60)
    response.raise_for_status()
 
    print(f"{report.summary['overall_status']}")
    print(f"posted temporary metadata: {response.json()}")
    print(f"view: {QC_VIEW_URL}?name={quote(RECORD_NAME)}")
 
 
if __name__ == "__main__":
    main()
 