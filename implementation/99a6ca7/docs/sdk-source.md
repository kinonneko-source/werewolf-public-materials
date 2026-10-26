# SDK 来源与固定版本

官方入口：https://www.krow.cn/docs （文档中心列出 PyPI: krow-agent-sdk；搜索索引读取成功，直接 HTML 为客户端页面，未从 HTML 提取链接）。

PyPI 元数据：https://pypi.org/pypi/krow-agent-sdk/0.9.5.27/json

项目主页：https://krow.cn

包：`krow-agent-sdk==0.9.5.27`；Python 要求 `>=3.11`。

发行物：https://files.pythonhosted.org/packages/00/5a/0eb9bee9835e0e96b26da2ae905700b3745a7db346960b36baff40e0a049/krow_agent_sdk-0.9.5.27-py3-none-any.whl

SHA-256：`96943a869bd30a309d7db6c8a4c50bb3cdfa7b7bcd807c4c57441bc2cb44e509`（下载后已逐字节核验）。

SDK 文档来源：https://github.com/aullik5/krow-sdk-docs ，由 PyPI 项目元数据指向。

文档固定 commit：`ee6ac7d3177c825e9103675234e67cf885fee348`。这是文档 commit，不代表 SDK wheel 的源码 commit；未获取私有引擎源码。

准备范围：wheel 与上游文档已在任务交付目录拉取；不把第三方 SDK 源码或二进制重复提交到此仓库。当前未安装依赖、未运行 SDK，尚未验证平台运行时兼容性。

可复现下载（仅固定 SDK 本体，不安装依赖）：

```sh
python3 -m pip download --no-deps --only-binary=:all: --require-hashes -r requirements-sdk.txt -d sdk-download
```

依赖仍由上游声明，尚无传递依赖锁；架构确定后再建立隔离运行环境。
