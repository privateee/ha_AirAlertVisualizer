"""Text -> structured events pipeline.

Import the concrete pieces from their modules to avoid an import cycle with
``dronevis.geo`` (the gazetteer needs ``parse.normalize.fold``)::

    from dronevis.parse.pipeline import Parser, ParsedEvent
    from dronevis.parse.normalize import fold, normalize
"""
