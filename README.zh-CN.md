# LoopCue

用于采样库、游戏音频构建流程的只读 WAV 循环与 cue 标记检查器。检查 `smpl` 循环帧范围、cue 偏移、关联标签/区域引用，并可在导出丢失全部预期循环或 cue 时使 CI 失败。不解码音频，不修改原文件，不访问网络，无运行时第三方依赖。

## 安装与完整演示

Python 3.11+，未发布到 PyPI：

```sh
git clone https://github.com/KanadeK/loopcue.git
cd loopcue
python -m venv .venv
```

PowerShell 使用 `.venv\Scripts\Activate.ps1`；macOS/Linux 使用 `source .venv/bin/activate`。

```sh
python -m pip install .
python examples/demo.py
python examples/demo.py --output-dir demo-out
loopcue demo-out/valid.wav --require-loop --require-cue --format text
```

默认演示生成真实的原创合成 WAV、调用已安装 CLI 验证结果，再自动删除临时文件。`--output-dir` 会保留三个示例，目录必须全新；不会覆盖已有目录。有效示例具有 8000 帧、一个循环和一个 cue，返回 0。

```sh
loopcue demo-out/bad-loop.wav --require-loop --format text
loopcue demo-out/stripped.wav --require-loop --require-cue --format text
```

后两条命令预期返回 1：第一份循环结束帧为 8000，而实际最后一帧是 7999；第二份缺失全部循环和 cue。`smpl` 结束点是**包含该帧**的索引。返回 2 表示参数、I/O、结构损坏或不支持的格式，此时不会输出半成品报告。

默认 JSON 包含格式、帧数、循环、cue、引用，以及带代码、字节位置和标记 ID 的问题。支持 `--format text`。报告不包含输入路径、音频数据或标签正文，但数值标记和格式信息仍属输入元数据，不是匿名化输出。

离线安装可从 [Release](https://github.com/KanadeK/loopcue/releases) 下载 wheel 与 SHA256SUMS，校验后使用 `python -m pip install --no-index --no-deps loopcue-0.1.0-py3-none-any.whl`。源码安装会下载构建工具。

## 支持范围

- 单个小端 RIFF-WAVE 数据流，PCM 8/16/24/32 位或 float 32/64 位，以及对应 extensible 子类型。检查块边界、长度和帧对齐。
- 检查循环编号唯一、类型 0/1/2、起止顺序与包含式结束边界；单帧循环合法。循环 ID 无需强制对应 cue。
- cue 限 `data` 相对地址、chunk/block 起始值均为零；偏移等于总帧数可作文件末尾边界标记。其他寻址产生问题，不能获得“检查通过”。
- 检查 adtl 标签、注释和文本区域的 cue 引用、终止字节与区域长度，不输出正文。
- 上限 2 GiB、10,000 个块、100,000 个循环/cue/引用。通过 seek 跳过音频。末尾奇数长度 data 缺少补齐字节可兼容；中间块和元数据仍要求补齐。

不支持 RF64、RIFX、压缩编码、分段音频和播放列表。未知循环类型产生问题。一次处理一个文件；不比对完整导出标记清单，不判断可闻接缝、不修复音频、不验证所有 BWF/INFO/fact 元数据或文本编码，也不保证所有播放器兼容。完整边界见 [英文 README](README.md) 和 [设计文档](docs/design.md)。

自动测试包括标准库 WAV 写入/读取交叉验证、损坏/截断文件、受限变异输入、32 MiB 音频 seek 验证与真实 CLI。实际记录见 [验证文档](docs/validation.md)。

卸载：`python -m pip uninstall loopcue`。默认演示自动清理；手动保留的 demo-out、报告、专用环境和源码目录需自行删除，无后台服务。MIT 许可。
