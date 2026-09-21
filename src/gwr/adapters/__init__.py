"""源内OSS の API への委譲アダプタ群（本番＝源内OSS の endpoint／テスト＝Fake）。

各ノード（llm/retrieval/code_interpreter）は ctx.adapters 経由でアダプタ実装を受け取る。
ノード自体は実装に依存せず、注入された Protocol にのみ依存する＝外部 I/O を完全に分離できる。
"""
