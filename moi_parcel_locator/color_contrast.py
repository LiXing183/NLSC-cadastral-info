"""Choose black or white text by WCAG contrast against an opaque sRGB color."""


def contrasting_text(color):
    def linear(channel):
        value = channel / 255.0
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    luminance = (0.2126 * linear(color.red()) + 0.7152 * linear(color.green())
                 + 0.0722 * linear(color.blue()))
    black = (luminance + 0.05) / 0.05
    white = 1.05 / (luminance + 0.05)
    return '#000000' if black >= white else '#ffffff'
