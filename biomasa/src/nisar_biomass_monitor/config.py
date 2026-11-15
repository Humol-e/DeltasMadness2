"""Configuración y parámetros calibrados del algoritmo NISAR L3 AGB ATBD."""

from __future__ import annotations

import configparser
from pathlib import Path
from typing import Tuple

from pydantic import BaseModel, Field


def find_repo_root(start: Path | None = None) -> Path:
    """Localiza la raíz `DeltasMadness2` subiendo desde el módulo o cwd."""
    candidates = [start or Path(__file__).resolve(), Path.cwd().resolve()]
    for base in candidates:
        for parent in (base, *base.parents):
            if (parent / "NISAR_Biomass" / "config" / "config.ini").is_file():
                return parent
    raise FileNotFoundError(
        "No se encontró NISAR_Biomass/config/config.ini en la jerarquía del repositorio."
    )


class NisarAtbdParameters(BaseModel):
    """Coeficientes calibrados de retrodispersión NISAR Banda L ([NISAR] en config.ini)."""

    A_HV: float = Field(gt=0.0)
    A_HH: float = Field(gt=0.0)
    B_HV: float = Field(gt=0.0)
    B_HH: float = Field(gt=0.0)
    C_HV: float = Field(gt=0.0)
    C_HH: float = Field(gt=0.0)
    alpha_HV: float
    alpha_HH: float
    delta_HV: float
    delta_HH: float
    D_HV: float = Field(gt=0.0)
    D_HH: float = Field(gt=0.0)
    s: float = Field(gt=0.0)

    def as_vector(self) -> tuple[float, ...]:
        """Devuelve el orden exacto de 13 parámetros usado por NISAR_Biomass/model_radar.py."""
        return (
            self.A_HV,
            self.A_HH,
            self.B_HV,
            self.B_HH,
            self.C_HV,
            self.C_HH,
            self.alpha_HV,
            self.alpha_HH,
            self.delta_HV,
            self.delta_HH,
            self.D_HV,
            self.D_HH,
            self.s,
        )


def load_nisar_config(config_path: Path | None = None) -> NisarAtbdParameters:
    """Lee `[NISAR]` desde `NISAR_Biomass/config/config.ini` sin importar GDAL/sklearn."""
    path = config_path or (find_repo_root() / "NISAR_Biomass" / "config" / "config.ini")
    if not path.is_file():
        raise FileNotFoundError(f"No existe el archivo de configuración NISAR: {path}")

    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    if "NISAR" not in parser:
        raise ValueError(f"El archivo {path} no contiene la sección [NISAR].")

    sec = parser["NISAR"]
    return NisarAtbdParameters(
        A_HV=float(sec["A_HV"]),
        A_HH=float(sec["A_HH"]),
        B_HV=float(sec["B_HV"]),
        B_HH=float(sec["B_HH"]),
        C_HV=float(sec["C_HV"]),
        C_HH=float(sec["C_HH"]),
        alpha_HV=float(sec["alpha_HV"]),
        alpha_HH=float(sec["alpha_HH"]),
        delta_HV=float(sec["delta_HV"]),
        delta_HH=float(sec["delta_HH"]),
        D_HV=float(sec["D_HV"]),
        D_HH=float(sec["D_HH"]),
        s=float(sec["s"]),
    )


class SiteDefinition(BaseModel):
    """Definición de un sitio piloto NISAR GCOV publicado hacia sar-Madness."""

    case_id: str
    site_code: str
    title: str
    subtitle: str
    region: str
    country: str
    s3_base_url: str
    s3_prefix: str
    epsg: int = 32616
    target_resolution_m: float = 100.0
    roi_bounds_utm: Tuple[float, float, float, float]
    roi_width_100m: int
    roi_height_100m: int
    pre_dates: tuple[str, ...]
    post_dates: tuple[str, ...]
    lulc_key: str
    classification_ref_key: str
    disturbance_ref_key: str
    nlcd_excluded_classes: tuple[int, ...] = (11, 12, 22, 23, 24)
    agb_max_mgha: float = 350.0


LENO_SITE = SiteDefinition(
    case_id="biomass-leno-nisar",
    site_code="LENO",
    title="Biomasa Forestal NISAR L3 — Lenoir Landing (LENO)",
    subtitle="Estimación de Biomasa Aérea (AGB) en Banda L (GCOV) y perturbación forestal 2021–2024",
    region="Alabama (Río Tombigbee / Sitio NEON LENO)",
    country="Estados Unidos",
    s3_base_url="https://s3.us-west-1.wasabisys.com/nisar-public-ebd",
    s3_prefix="ATBD/ecosystems/disturbance/sites/leno/",
    epsg=32616,
    target_resolution_m=100.0,
    # Malla de validación oficial LENO 100 m UTM 16N (210 x 193 ha = 405.30 km²)
    roi_bounds_utm=(378225.0, 3548756.0, 399225.0, 3568056.0),
    roi_width_100m=210,
    roi_height_100m=193,
    pre_dates=("20220930",),
    post_dates=("20230929",),
    lulc_key="ATBD/ecosystems/disturbance/sites/leno/LENO_LULC.tif",
    classification_ref_key=(
        "ATBD/ecosystems/disturbance/sites/leno/validation_reference/"
        "LENO_100m_classification_20251002.tif"
    ),
    disturbance_ref_key=(
        "ATBD/ecosystems/disturbance/sites/leno/validation_reference/"
        "PLANET_VAL_FINALDIST_100M_LENO_20220930_20230918_20251002.tif"
    ),
)
