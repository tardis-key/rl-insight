# verl: Volcano Engine Reinforcement Learning for LLMs
<div align="center">

<a href="https://deepwiki.com/volcengine/verl"><img src="https://devin.ai/assets/deepwiki-badge.png" alt="Ask DeepWiki.com" style="height:20px;"></a>
[![GitHub Repo stars](https://img.shields.io/github/stars/volcengine/verl)](https://github.com/volcengine/verl/stargazers)
[![Twitter](https://img.shields.io/twitter/follow/verl_project)](https://twitter.com/verl_project)
<a href="https://join.slack.com/t/verl-project/shared_invite/zt-3c6mc2khw-v0lo6NfDPuFP6OnkrZwfqw"><img src="https://img.shields.io/badge/Slack-verl-blueviolet?logo=slack&amp"></a>
<a href="https://arxiv.org/pdf/2409.19256"><img src="https://img.shields.io/static/v1?label=EuroSys&message=Paper&color=red"></a>
[![Documentation](https://img.shields.io/badge/documentation-blue)](https://verl.readthedocs.io/en/latest/)
<a href="https://raw.githubusercontent.com/eric-haibin-lin/verl-community/refs/heads/main/WeChat.JPG"><img src="https://img.shields.io/badge/微信-green?logo=wechat&amp"></a>

</div>

verl is a flexible, efficient and production-ready RL training library for large language models (LLMs).

verl is the open-source version of **[HybridFlow: A Flexible and Efficient RLHF Framework](https://arxiv.org/abs/2409.19256v2)** paper.

introduction of rl-insight

- 1
- 2

<div align="center">
 <img src="https://github.com/tardis-key/rl-insight/blob/main/assets/rl_insight_framework.svg" width="400" alt="rl-insight-arch.png">
</div>

## Key Features

- **FSDP**, **FSDP2** and **Megatron-LM** for training.
- **vLLM**, **SGLang** and **HF Transformers** for rollout generation.
- Compatible with Hugging Face Transformers and Modelscope Hub: [Qwen-3](https://github.com/volcengine/verl/blob/main/examples/grpo_trainer/run_qwen3-8b.sh), Qwen-2.5, Llama3.1, Gemma2, DeepSeek-LLM, etc

## Upcoming Features and Changes

- Q3 Roadmap https://github.com/volcengine/verl/issues/2388

## Getting Started

<a href="https://verl.readthedocs.io/en/latest/index.html"><b>Documentation</b></a>

**Quickstart:**

- [Installation](https://verl.readthedocs.io/en/latest/start/install.html)
- [Quickstart](https://verl.readthedocs.io/en/latest/start/quickstart.html)
- [Programming Guide](https://verl.readthedocs.io/en/latest/hybrid_flow.html) & [Tech Talk](https://hcqnc.xetlk.com/sl/3vACOK) (in Chinese)
- [PPO in verl](https://verl.readthedocs.io/en/latest/algo/ppo.html)
- [GRPO in verl](https://verl.readthedocs.io/en/latest/algo/grpo.html)

## Contribution Guide

See [contributions guide](CONTRIBUTING.md)
