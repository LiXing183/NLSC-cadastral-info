def classFactory(iface):
    from .parcel_locator import MOIParcelLocatorPlugin
    return MOIParcelLocatorPlugin(iface)
