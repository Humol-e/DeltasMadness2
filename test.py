
import ee

# 1. Correo de la cuenta de servicio y ruta al JSON
SERVICE_ACCOUNT_EMAIL = "gee-backend-account@sarshack.iam.gserviceaccount.com"
KEY_FILE_PATH = "./config/greecreds.json"

# 2. Cargar credenciales e inicializar
credentials = ee.ServiceAccountCredentials(SERVICE_ACCOUNT_EMAIL, KEY_FILE_PATH)
ee.Initialize(credentials=credentials)

# 3. Ejemplo: Generar el MapId para Mapbox GL JS
def get_sar_tile_url():
    # Cargar colección de Sentinel-1 en GEE
    s1 = ee.ImageCollection('COPERNICUS/S1_GRD') \
           .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV')) \
           .filter(ee.Filter.eq('instrumentMode', 'IW')) \
           .first()
    
    # Parámetros de visualización
    viz_params = {'min': -25, 'max': 0, 'bands': ['VV']}
    
    # Obtener plantilla de URL de teselas (XYZ)
    map_id = s1.getMapId(viz_params)
    return map_id['tile_fetcher'].url_format
if __name__ == "__main__":
    print(get_sar_tile_url())
