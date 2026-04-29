"""agora — Market data ingestion and local Parquet storage for Massive.com data.

Public surface:
    - agora.config.MassiveConfig
    - agora.client.MassiveClient, get_client
    - agora.loaders.parquet.FlatFileLoader (read local Parquet)
    - agora.loaders.rest.MassiveDataApi (live REST access)
    - agora.download (bulk historical download CLI/library)
"""

__version__ = "0.1.0"