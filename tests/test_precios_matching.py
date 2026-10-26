import pytest

from dpdf import config
from dpdf.processing.matching import contiene, evaluar, normalizar
from dpdf.processing.precios import parsear_precio


@pytest.mark.parametrize("texto, valor, maximo, moneda", [
    ("$1,299.00", 1299.00, None, "MXN"),
    ("$229", 229.0, None, "MXN"),
    ("MX$1.299,00", 1299.00, None, "MXN"),
    ("MX$ 89,50", 89.50, None, "MXN"),
    ("US $12.34", 12.34, None, "USD"),
    ("US$2.10-3.40", 2.10, 3.40, "USD"),
    ("$2.50 - $3.10", 2.50, 3.10, "MXN"),
    ("199 - 259", 199.0, 259.0, "MXN"),
    ("$1,099.50 $1,800.00", 1099.50, None, "MXN"),   # precio actual seguido del anterior
    ("12,345.67", 12345.67, None, "MXN"),
])
def test_parsear_precio(texto, valor, maximo, moneda):
    p = parsear_precio(texto)
    assert p.valor == pytest.approx(valor)
    assert p.valor_max == (pytest.approx(maximo) if maximo else None)
    assert p.moneda == moneda


def test_parsear_precio_numero_y_vacios():
    assert parsear_precio(189.5, "MXN").valor == 189.5
    assert parsear_precio(None) is None
    assert parsear_precio("Agotado") is None
    assert parsear_precio(0) is None


def test_normalizar():
    assert normalizar("Batería 10000mAh USB-C") == "bateria 10000 mah usb c"
    assert normalizar("Ratón 2.4GHz.") == "raton 2.4 ghz"
    assert contiene(normalizar("Logitech M170 Gris"), "m170")
    assert not contiene(normalizar("Logitech M1700"), "m170")


@pytest.mark.parametrize("producto_id, titulo, precio, nivel", [
    ("mouse", "Mouse Inalámbrico Logitech M170 Gris", 229, "exacto"),
    ("mouse", "Logitech M170", 45, None),                                   # precio absurdo para el modelo
    ("mouse", "Mouse Pad Logitech", 159, None),                             # excluido
    ("mouse", "Ratón Inalámbrico Ergonómico Silencioso", 99, "equivalente"),
    ("mouse", "Mouse alámbrico USB", 99, None),                             # no es inalámbrico
    ("teclado", "Redragon K552 Kumara Teclado Mecánico RGB", 749, "exacto"),
    ("teclado", "Combo Teclado y Mouse Redragon", 749, None),
    ("audifonos", "Xiaomi Redmi Buds 6 Active Audífonos Bluetooth 5.4", 379, "exacto"),
    ("audifonos", "Audífonos TWS Bluetooth 5.3 con estuche de carga", 89, "equivalente"),
    ("power_bank", "Xiaomi Power Bank 10000mAh 22.5W", 399, "exacto"),
    ("power_bank", "Batería portátil 10,000 mAh carga rápida", 259, "equivalente"),
    ("power_bank", "Batería portátil 20000 mAh", 259, None),                # otra capacidad
    ("microsd", "SanDisk Ultra microSDXC 128GB 140MB/s", 189, "exacto"),
    ("microsd", "SanDisk Ultra microSDXC 64GB", 129, None),
    ("smartband", "Xiaomi Smart Band 9 Pulsera Inteligente", 749, "exacto"),
    ("smartband", "Xiaomi Smart Band 9 Pro", 1099, "equivalente"),          # otra versión
    ("smartband", "Correa para Xiaomi Smart Band 9", 89, None),
    ("cargador", "UGREEN Cargador USB C 20W PD Carga Rápida", 229, "exacto"),
    ("cargador", "Cargador de auto USB C 20W", 149, None),
    ("bocina", "JBL Go 4 Bocina Bluetooth Portátil", 849, "exacto"),
    ("hub_usbc", "UGREEN Hub USB C 5 en 1 HDMI 4K", 499, "exacto"),
    ("cable_usbc", "UGREEN Cable USB C a USB C 60W 1m", 149, "exacto"),
    ("cable_usbc", "Cable Lightning a USB C 60W", 149, None),
])
def test_matching(producto_id, titulo, precio, nivel):
    assert evaluar(titulo, precio, config.producto(producto_id)).nivel == nivel


def test_matching_mayoreo_ignora_rango():
    r = evaluar("Wireless Mouse 2.4G Optical OEM", 38.0, config.producto("mouse"), es_mayoreo=True)
    assert r.nivel == "equivalente"
