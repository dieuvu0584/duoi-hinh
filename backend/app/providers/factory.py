from app.core.config import Settings
from app.providers.contracts import ReelsPublishingProvider
from app.providers.dryrun import DryRunReels
from app.providers.meta_reels import MetaReelsProvider


def get_reels_provider(cfg: Settings) -> ReelsPublishingProvider:
    """`live` needs an explicit setting AND credentials; anything else is the dry-run provider."""
    if cfg.publish_mode == "live":
        token = cfg.meta_access_token.get_secret_value() if cfg.meta_access_token else ""
        if not cfg.meta_page_id or not token:
            raise RuntimeError("NHD_PUBLISH_MODE=live needs META_PAGE_ID and META_ACCESS_TOKEN")
        return MetaReelsProvider(page_id=cfg.meta_page_id, access_token=token,
                                 api_version=cfg.meta_api_version)
    return DryRunReels()
