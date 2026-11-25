# colibri (https://github.com/JustVugg/colibri) for Olares One, CUDA backend.
# Builds the qwen38 engine (Qwen3.8-Flash-Next and its GGUF variants) for
# sm_80..sm_120 (RTX 50-series / Blackwell needs CUDA >= 12.8) and ships the
# launcher, the OpenAI/Anthropic gateway and the browser chat UI.
# The model is NOT baked in: /boot/colibri_boot.py downloads it to the
# persistent volume (/models) on first start, resumably, with progress in logs.

ARG CUDA_VERSION=12.8.1
FROM nvidia/cuda:${CUDA_VERSION}-devel-ubuntu24.04 AS build
ARG COLIBRI_REF=e479104ae01bdc40ce81e675299b1d230891b321
ARG ARCH=x86-64-v3
RUN apt-get update && apt-get install -y --no-install-recommends \
      git ca-certificates build-essential make python3 && \
    rm -rf /var/lib/apt/lists/*
WORKDIR /opt
RUN git clone https://github.com/JustVugg/colibri.git colibri && \
    cd colibri && git checkout "${COLIBRI_REF}" && rm -rf .git
WORKDIR /opt/colibri/c
# CUDA_ARCH=portable: SASS for sm_80/86/89/90/120 + compute_120 PTX (no GPU on the builder)
RUN make -j"$(nproc)" qwen38 CUDA=1 CUDA_ARCH=portable ARCH=${ARCH} && \
    ls -la qwen38 && rm -f *.o *.d

FROM nvidia/cuda:${CUDA_VERSION}-runtime-ubuntu24.04
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 libgomp1 ca-certificates tini && \
    rm -rf /var/lib/apt/lists/* && \
    (id -u 1000 >/dev/null 2>&1 && userdel -r "$(id -un 1000)" || true) && \
    useradd -m -u 1000 colibri
COPY --from=build /opt/colibri /opt/colibri
COPY docker/colibri_boot.py /boot/colibri_boot.py
ENV PYTHONUNBUFFERED=1 \
    COLI_MODEL_ID=qwen38-flash-next \
    COLI_MODELS_DIR=/models \
    COLI_PORT=8000 \
    COLI_UI_PORT=8790 \
    HOME=/home/colibri
USER 1000
WORKDIR /opt/colibri
EXPOSE 8000 8790
ENTRYPOINT ["/usr/bin/tini", "--", "python3", "/boot/colibri_boot.py"]
