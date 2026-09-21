"""serve — /invoke を出す HTTP 層（源内OSS の Web プロトコル準拠の AI アプリ化）。

源内OSS の Web が `{inputs}` を POST し、本サービスが `{outputs, artifacts}` を返す。
起動時に全フローを検証し、不正なら fail-closed で起動を拒否する。

FastAPI は任意依存（`pip install gwr[serve]`）。本モジュールは import 時に FastAPI を要求する。

注意：FastAPI のパラメータ内省は実体の型注釈を要求するため、本モジュールでは
`from __future__ import annotations`（注釈の文字列化）を使わない。
"""

from typing import Annotated, Any

from gwr.app import WorkflowApp


def create_app(
    workflow: WorkflowApp,
    validate_on_startup: bool = True,
    api_key: str | None = None,
) -> Any:
    """WorkflowApp を /invoke エンドポイントとして公開する FastAPI アプリを返す。

    api_key を渡すと、源内OSS の Web の ExApp 呼び出しと同じ `x-api-key` ヘッダを検証する
    （一致しなければ 401）。None なら認証なし（ローカル確認用）。
    """
    from fastapi import FastAPI, Header, HTTPException  # 遅延 import（任意依存）
    from pydantic import BaseModel, Field

    if validate_on_startup:
        report = workflow.validate()
        if not report.ok:
            raise RuntimeError(f"フロー検証に失敗（起動拒否）: {report.issues}")

    class InvokeRequest(BaseModel):
        """源内OSS の Web からの送出形式 {inputs: {...}}。"""

        inputs: dict[str, Any] = Field(default_factory=dict)

    def _check_auth(provided: str | None) -> None:
        if api_key is not None and provided != api_key:
            raise HTTPException(status_code=401, detail="invalid api key")

    api = FastAPI(title="gwr")

    @api.get("/ui-spec")
    def ui_spec() -> dict[str, Any]:
        """源内OSS のチーム管理に登録するリクエスト形式 JSON を返す。"""
        return workflow.ui_spec()

    @api.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @api.post("/invoke")
    def invoke(
        payload: InvokeRequest,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        _check_auth(x_api_key)
        return workflow.invoke(payload.inputs)

    return api
