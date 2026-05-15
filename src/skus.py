"""ReviewLens SKU catalog.

The curated 10-SKU mix for the prototype. Five Shark, five Ninja, stratified across
categories so a defect signal lands on a specific component class (motor, heating
element, blade, battery, seal, display).

Fill in the ASIN field for each SKU before running the scraper. ASINs are the
10-character alphanumeric IDs from the Amazon URL (the bit after /dp/).

Selection principle: include both highly-rated and mixed-review products so the
extractor isn't trained on a self-selected slice of complaints. If every SKU
already has a defect reputation, every model looks great on the eval set.
"""

SKUS = [
    # ---- Shark ----
    {
        "asin": "",  # TODO: fill in
        "brand": "Shark",
        "category": "upright_vacuum",
        "display_name": "Shark Vertex Pro",
        "notes": "Flagship upright. Motor + brushroll are the obvious failure components.",
    },
    {
        "asin": "",  # TODO: fill in
        "brand": "Shark",
        "category": "robot_vacuum",
        "display_name": "Shark IQ Robot",
        "notes": "Robot vacuum. Battery, wheels, sensors, dock charging are the surfaces.",
    },
    {
        "asin": "",  # TODO: fill in
        "brand": "Shark",
        "category": "cordless_stick",
        "display_name": "Shark Stratos Cordless",
        "notes": "Cordless stick. Battery degradation is the typical 6-month signal.",
    },
    {
        "asin": "",  # TODO: fill in
        "brand": "Shark",
        "category": "upright_vacuum",
        "display_name": "Shark Navigator",
        "notes": "High-volume entry-level upright. Lots of reviews, mixed quality.",
    },
    {
        "asin": "",  # TODO: fill in
        "brand": "Shark",
        "category": "steam_mop",
        "display_name": "Shark Steam Mop",
        "notes": "Different mechanical class. Heating element, water reservoir, pads.",
    },

    # ---- Ninja ----
    {
        "asin": "",  # TODO: fill in
        "brand": "Ninja",
        "category": "ice_cream_maker",
        "display_name": "Ninja Creami",
        "notes": "Viral product with high review volume. Motor + paddle + pint container.",
    },
    {
        "asin": "",  # TODO: fill in
        "brand": "Ninja",
        "category": "multi_cooker",
        "display_name": "Ninja Foodi",
        "notes": "Pressure cooker + air fryer. Sealing ring, heating element, lid latch.",
    },
    {
        "asin": "",  # TODO: fill in
        "brand": "Ninja",
        "category": "blender",
        "display_name": "Ninja Professional Blender",
        "notes": "Workhorse blender. Blade, motor base, jar gasket are typical complaints.",
    },
    {
        "asin": "",  # TODO: fill in
        "brand": "Ninja",
        "category": "coffee_maker",
        "display_name": "Ninja Coffee Maker",
        "notes": "Different category. Heating element, carafe, water reservoir, valves.",
    },
    {
        "asin": "",  # TODO: fill in
        "brand": "Ninja",
        "category": "air_fryer",
        "display_name": "Ninja Air Fryer",
        "notes": "Single-function basic model. Heating element, fan, basket coating.",
    },
]


def get_skus_with_asins() -> list[dict]:
    """Return only SKUs that have an ASIN filled in. Raises if none."""
    valid = [s for s in SKUS if s.get("asin")]
    if not valid:
        raise ValueError(
            "No ASINs filled in yet. Edit src/skus.py and add the 10-character "
            "Amazon ASINs for each product."
        )
    return valid
