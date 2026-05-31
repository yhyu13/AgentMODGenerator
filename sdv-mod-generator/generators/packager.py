"""ZIP packager — stub."""
import structlog

logger = structlog.get_logger()


def package(request_id: str, files: dict[str, dict], assets: list[str]) -> str:
    """Package files + assets into a Content Patcher zip.
    
    Stub: just logs and returns mock zip key.
    Returns:
        S3 key for the zip file.
    """
    logger.info("packager.run", request_id=request_id, file_count=len(files), asset_count=len(assets))
    zip_key = f"mods/{request_id}/{request_id}.zip"
    logger.info("packager.done", request_id=request_id, zip_key=zip_key)
    return zip_key
