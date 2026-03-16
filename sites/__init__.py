from sites import vinted, ebay, depop

SITE_MODULES = {
    "vinted": vinted,
    "ebay": ebay,
    "depop": depop,
}

SUPPORTED_SITES = list(SITE_MODULES.keys())


def get_site(name: str):
    mod = SITE_MODULES.get(name)
    if not mod:
        raise ValueError(f"Unknown site '{name}'. Supported: {SUPPORTED_SITES}")
    return mod
