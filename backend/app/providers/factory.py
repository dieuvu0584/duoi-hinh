from app.core.config import Settings
from app.providers.contracts import ReelsPublishingProvider
from app.providers.dryrun import DryRunReels
from app.providers.meta_reels import MetaReelsProvider
from app.providers.youtube_videos import YouTubeProvider


def get_reels_provider(cfg: Settings, page_id: str | None = None) -> ReelsPublishingProvider:
    """`live` needs an explicit setting AND credentials; anything else is the dry-run provider."""
    if cfg.publish_mode == "live":
        token = cfg.meta_access_token.get_secret_value() if cfg.meta_access_token else ""
        page = page_id or cfg.meta_page_id
        if not page or not token:
            raise RuntimeError("NHD_PUBLISH_MODE=live needs a Page ID (admin setting or "
                               "META_PAGE_ID) and META_ACCESS_TOKEN")
        return MetaReelsProvider(page_id=page, access_token=token,
                                 api_version=cfg.meta_api_version)
    return DryRunReels()


def get_youtube_provider(cfg: Settings):
    """Live YouTube needs the OAuth client and refresh token; otherwise the dry-run provider."""
    if cfg.publish_mode == "live":
        secret = cfg.youtube_client_secret.get_secret_value() if cfg.youtube_client_secret else ""
        refresh = cfg.youtube_refresh_token.get_secret_value() if cfg.youtube_refresh_token else ""
        if not cfg.youtube_client_id or not secret or not refresh:
            raise RuntimeError("NHD_PUBLISH_MODE=live needs YOUTUBE_CLIENT_ID, "
                               "YOUTUBE_CLIENT_SECRET and YOUTUBE_REFRESH_TOKEN for YouTube")
        return YouTubeProvider(client_id=cfg.youtube_client_id, client_secret=secret,
                               refresh_token=refresh)
    return DryRunReels()


def get_providers(cfg: Settings, page_id: str | None = None) -> dict[str, object]:
    """Platform -> provider. YouTube is left out when its credentials are missing in live mode,
    so a half-configured YouTube can never block or break Facebook publishing."""
    providers: dict[str, object] = {"facebook": get_reels_provider(cfg, page_id)}
    try:
        providers["youtube"] = get_youtube_provider(cfg)
    except RuntimeError:
        pass
    return providers
