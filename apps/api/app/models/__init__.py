"""
Import every model here so Alembic's `target_metadata` (which points at
Base.metadata) sees all tables during autogenerate. Every future phase that
adds a model MUST import it in this file.
"""

from app.models.organization import Organization
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember, WorkspaceRole
from app.models.data_connection import DataConnection, ConnectionType
from app.models.dataset import Dataset, DatasetSourceType
from app.models.dataset_version import DatasetVersion, DatasetVersionStatus
from app.models.dashboard import Dashboard
from app.models.dashboard_widget import DashboardWidget, WidgetType
from app.models.ml_model import MLModel, MLTaskType, MLModelStatus
from app.models.forecast import Forecast, ForecastFrequency, ForecastStatus
from app.models.report import Report, ReportFormat, ReportStatus
from app.models.workspace_invite import WorkspaceInvite, WorkspaceInviteStatus
from app.models.dashboard_comment import DashboardComment
from app.models.activity_log import ActivityLog
from app.models.dashboard_version import DashboardVersion

__all__ = [
    "Organization",
    "User",
    "Workspace",
    "WorkspaceMember",
    "WorkspaceRole",
    "DataConnection",
    "ConnectionType",
    "Dataset",
    "DatasetSourceType",
    "DatasetVersion",
    "DatasetVersionStatus",
    "Dashboard",
    "DashboardWidget",
    "WidgetType",
    "MLModel",
    "MLTaskType",
    "MLModelStatus",
    "Forecast",
    "ForecastFrequency",
    "ForecastStatus",
    "Report",
    "ReportFormat",
    "ReportStatus",
    "WorkspaceInvite",
    "WorkspaceInviteStatus",
    "DashboardComment",
    "ActivityLog",
    "DashboardVersion",
]
