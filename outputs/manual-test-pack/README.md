# Run the NetConfig SLM GGUF in Ollama

GGUF file: `netconfig-slm-qwen3-4b-q4_k_m.gguf` (Q4_K_M quant of the merged
Qwen3-4B fine-tune). Put it in this directory, then create a Modelfile:

```
FROM ./netconfig-slm-qwen3-4b-q4_k_m.gguf
TEMPLATE """{{ if .System }}<|im_start|>system
{{ .System }}<|im_end|>
{{ end }}{{ range .Messages }}<|im_start|>{{ .Role }}
{{ .Content }}<|im_end|>
{{ end }}<|im_start|>assistant
"""
PARAMETER stop "<|im_end|>"
PARAMETER stop "<|im_start|>"
```

Then:

```
ollama create netconfig-slm -f Modelfile
ollama run netconfig-slm
```

Paste an intent from `intents.md` as the user message; the model should reply
with exactly one Cisco IOS XE config and stop. Score yourself against
`answers.md` (same numbering).
