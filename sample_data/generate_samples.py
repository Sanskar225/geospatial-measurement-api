import os
import zipfile
from pathlib import Path
import shapefile

SAMPLE_DIR = Path(__file__).parent
SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

# 1. Generate KML Sample
kml_content = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Aereo Bengaluru Drone Survey Mission</name>
    <description>Drone orthomosaic survey boundary, flight path, and ground control points</description>
    
    <!-- Polygon: Solar Plant / Survey Site Boundary -->
    <Placemark>
      <name>Solar Plant Sector A</name>
      <description>Perimeter boundary for solar farm site</description>
      <ExtendedData>
        <Data name="Project">
          <value>Aereo CleanEnergy Phase 1</value>
        </Data>
        <Data name="SurveyDate">
          <value>2026-10-08</value>
        </Data>
      </ExtendedData>
      <Polygon>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>
              77.5850,12.9700,0
              77.5950,12.9700,0
              77.5950,12.9800,0
              77.5850,12.9800,0
              77.5850,12.9700,0
            </coordinates>
          </LinearRing>
        </outerBoundaryIs>
      </Polygon>
    </Placemark>

    <!-- LineString: Power Line / Road Corridor Alignment -->
    <Placemark>
      <name>Transmission Line Corridor</name>
      <description>High voltage transmission line survey corridor</description>
      <ExtendedData>
        <Data name="Voltage">
          <value>66kV</value>
        </Data>
      </ExtendedData>
      <LineString>
        <coordinates>
          77.5800,12.9650,0
          77.5860,12.9720,0
          77.5920,12.9780,0
          77.6000,12.9850,0
        </coordinates>
      </LineString>
    </Placemark>

    <!-- Point: Ground Control Point (GCP) -->
    <Placemark>
      <name>GCP Base Station #1</name>
      <description>RTK Base station location</description>
      <Point>
        <coordinates>77.5900,12.9750,920.5</coordinates>
      </Point>
    </Placemark>

    <!-- Polygon with an Interior Hole (Quarry Ring) -->
    <Placemark>
      <name>Mining Pit Exclusion Zone</name>
      <description>Mining pit buffer with interior extraction hole</description>
      <Polygon>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>
              77.6000,12.9600,0
              77.6150,12.9600,0
              77.6150,12.9750,0
              77.6000,12.9750,0
              77.6000,12.9600,0
            </coordinates>
          </LinearRing>
        </outerBoundaryIs>
        <innerBoundaryIs>
          <LinearRing>
            <coordinates>
              77.6040,12.9640,0
              77.6100,12.9640,0
              77.6100,12.9700,0
              77.6040,12.9700,0
              77.6040,12.9640,0
            </coordinates>
          </LinearRing>
        </innerBoundaryIs>
      </Polygon>
    </Placemark>

  </Document>
</kml>
"""

with open(SAMPLE_DIR / "survey_sample.kml", "w", encoding="utf-8") as f:
    f.write(kml_content)

print("Created survey_sample.kml")

# 2. Generate Sample Shapefile (.zip)
shp_base_name = "sample_survey_cadastre"
shp_temp_dir = SAMPLE_DIR / "temp_shp"
shp_temp_dir.mkdir(exist_ok=True)

# Create Polygon Shapefile using pyshp
w = shapefile.Writer(str(shp_temp_dir / shp_base_name), shapeType=shapefile.POLYGON)
w.field("NAME", "C", size=50)
w.field("LAND_USE", "C", size=30)
w.field("PARCEL_ID", "N", size=10)

# Feature 1: Industrial Parcel
w.poly([
    [
        [77.50, 13.00],
        [77.52, 13.00],
        [77.52, 13.02],
        [77.50, 13.02],
        [77.50, 13.00]
    ]
])
w.record("Industrial Park Alpha", "Commercial", 101)

# Feature 2: Agricultural Parcel
w.poly([
    [
        [77.53, 13.03],
        [77.55, 13.03],
        [77.55, 13.05],
        [77.53, 13.05],
        [77.53, 13.03]
    ]
])
w.record("Agri Zone Beta", "Agricultural", 102)

w.close()

# Write WGS 84 .prj file (Standard EPSG:4326 WKT)
prj_wkt = 'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]'
with open(shp_temp_dir / f"{shp_base_name}.prj", "w", encoding="utf-8") as f:
    f.write(prj_wkt)

# Pack into zip file
zip_path = SAMPLE_DIR / "sample_shapefile.zip"
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zip_out:
    for ext in [".shp", ".shx", ".dbf", ".prj"]:
        file_to_pack = shp_temp_dir / f"{shp_base_name}{ext}"
        if file_to_pack.exists():
            zip_out.write(file_to_pack, arcname=f"{shp_base_name}{ext}")

# Clean up temp files
import shutil
shutil.rmtree(shp_temp_dir)

print("Created sample_shapefile.zip")
