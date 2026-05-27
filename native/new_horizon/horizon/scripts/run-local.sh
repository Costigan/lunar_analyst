#!/usr/bin/env bash
set -euo pipefail

#IMAGE_TAG="${IMAGE_TAG:-lunar-horizon:local}"
IMAGE_TAG="lunar-horizon:local"
#IMAGE_TAG="gitlab-registry.nrp-nautilus.io/costigan/lunar_analyst:latest"
CONTAINER_ROOT="/workspace"

usage() {
  cat <<USAGE
Usage:
  $(basename "$0") <host_data_root> make <horizons_rel_path> <offset> <stride> [--gpu-concurrency <count>] <dem_rel_path...>
  $(basename "$0") <host_data_root> psr  <horizons_rel_path> <dem_rel_path> <output_rel_path>

Arguments:
  host_data_root     Host directory mounted to /workspace.

Verb: make
  horizons_rel_path  Relative path to existing horizons output directory under host_data_root.
  offset             Patch shard offset (int >= 0 and < stride).
  stride             Patch shard stride (int > 0).
  --gpu-concurrency  Optional GPU worker/stream count (int > 0, default: 4).
  dem_rel_path...    One or more DEM paths relative to host_data_root.

Verb: psr
  horizons_rel_path  Relative path to existing horizons directory under host_data_root.
  dem_rel_path       DEM path relative to host_data_root.
  output_rel_path    Output TIFF path relative to host_data_root.

Environment:
  IMAGE_TAG          Docker image tag (default: lunar-horizon:local)

Examples:
  $(basename "$0") /e/lunar_analyst_docker_test make scenario/horizons 0 16 --gpu-concurrency 4 scenario/dems/haworth.tif scenario/dems/LDEM_80S_20M-2017-06-15-processed.tif
  $(basename "$0") /e/lunar_analyst_docker_test psr scenario/horizons scenario/dems/haworth.tif scenario/lighting/psr.tif
USAGE
}

require_relative_path() {
  local path="$1"
  local label="$2"

  if [[ -z "${path}" ]]; then
    echo "${label} must not be empty" >&2
    exit 1
  fi

  if [[ "${path}" = /* ]]; then
    echo "${label} must be relative to host_data_root: ${path}" >&2
    exit 1
  fi

  if [[ "${path}" == *".."* ]]; then
    echo "${label} must not contain '..': ${path}" >&2
    exit 1
  fi
}

if [[ $# -lt 2 ]]; then
  usage
  exit 1
fi

HOST_DATA_ROOT="$1"
VERB="$2"
shift 2

if [[ ! -d "${HOST_DATA_ROOT}" ]]; then
  echo "Host data root does not exist: ${HOST_DATA_ROOT}" >&2
  exit 1
fi

case "${VERB}" in
  make)
    if [[ $# -lt 4 ]]; then
      usage
      exit 1
    fi

    HORIZONS_REL="$1"
    OFFSET="$2"
    STRIDE="$3"
    shift 3
    DEM_RELS=()
    CONTAINER_MAKE_ARGS=(
      make
      "${CONTAINER_ROOT}/${HORIZONS_REL}"
      "${OFFSET}"
      "${STRIDE}"
    )

    require_relative_path "${HORIZONS_REL}" "horizons_rel_path"

    if [[ ! "${OFFSET}" =~ ^-?[0-9]+$ ]] || [[ "${OFFSET}" -lt 0 ]]; then
      echo "offset must be an integer >= 0: ${OFFSET}" >&2
      exit 1
    fi

    if [[ ! "${STRIDE}" =~ ^[0-9]+$ ]] || [[ "${STRIDE}" -le 0 ]]; then
      echo "stride must be an integer > 0: ${STRIDE}" >&2
      exit 1
    fi

    if [[ "${OFFSET}" -ge "${STRIDE}" ]]; then
      echo "offset must be less than stride: offset=${OFFSET}, stride=${STRIDE}" >&2
      exit 1
    fi

    if [[ ! -d "${HOST_DATA_ROOT}/${HORIZONS_REL}" ]]; then
      echo "Horizon output directory must already exist: ${HOST_DATA_ROOT}/${HORIZONS_REL}" >&2
      exit 1
    fi

    while [[ $# -gt 0 ]]; do
      case "$1" in
        --gpu-concurrency)
          if [[ $# -lt 2 ]]; then
            echo "--gpu-concurrency requires an integer value" >&2
            exit 1
          fi
          if [[ ! "$2" =~ ^[0-9]+$ ]] || [[ "$2" -le 0 ]]; then
            echo "gpu concurrency must be an integer > 0: $2" >&2
            exit 1
          fi
          CONTAINER_MAKE_ARGS+=("--gpu-concurrency" "$2")
          shift 2
          ;;
        --gpu-concurrency=*)
          GPU_CONCURRENCY="${1#--gpu-concurrency=}"
          if [[ ! "${GPU_CONCURRENCY}" =~ ^[0-9]+$ ]] || [[ "${GPU_CONCURRENCY}" -le 0 ]]; then
            echo "gpu concurrency must be an integer > 0: ${GPU_CONCURRENCY}" >&2
            exit 1
          fi
          CONTAINER_MAKE_ARGS+=("$1")
          shift
          ;;
        --*)
          echo "Unknown make option: $1" >&2
          exit 1
          ;;
        *)
          DEM_RELS+=("$1")
          shift
          ;;
      esac
    done

    if [[ "${#DEM_RELS[@]}" -eq 0 ]]; then
      echo "At least one DEM path is required for make" >&2
      exit 1
    fi

    for dem_rel in "${DEM_RELS[@]}"; do
      require_relative_path "${dem_rel}" "dem_rel_path"
      if [[ ! -f "${HOST_DATA_ROOT}/${dem_rel}" ]]; then
        echo "DEM file does not exist: ${HOST_DATA_ROOT}/${dem_rel}" >&2
        exit 1
      fi
      CONTAINER_MAKE_ARGS+=("${CONTAINER_ROOT}/${dem_rel}")
    done

    docker run --rm \
      --gpus all \
      -v "${HOST_DATA_ROOT}:${CONTAINER_ROOT}" \
      "${IMAGE_TAG}" \
      "${CONTAINER_MAKE_ARGS[@]}"
    ;;

  psr)
    if [[ $# -ne 3 ]]; then
      usage
      exit 1
    fi

    HORIZONS_REL="$1"
    DEM_REL="$2"
    OUTPUT_REL="$3"

    require_relative_path "${HORIZONS_REL}" "horizons_rel_path"
    require_relative_path "${DEM_REL}" "dem_rel_path"
    require_relative_path "${OUTPUT_REL}" "output_rel_path"

    if [[ ! -d "${HOST_DATA_ROOT}/${HORIZONS_REL}" ]]; then
      echo "Horizon directory must already exist: ${HOST_DATA_ROOT}/${HORIZONS_REL}" >&2
      exit 1
    fi

    if [[ ! -f "${HOST_DATA_ROOT}/${DEM_REL}" ]]; then
      echo "DEM file does not exist: ${HOST_DATA_ROOT}/${DEM_REL}" >&2
      exit 1
    fi

    OUTPUT_HOST_DIR="$(dirname "${HOST_DATA_ROOT}/${OUTPUT_REL}")"
    mkdir -p "${OUTPUT_HOST_DIR}"

    docker run --rm \
      --gpus all \
      -v "${HOST_DATA_ROOT}:${CONTAINER_ROOT}" \
      "${IMAGE_TAG}" \
      psr \
      "${CONTAINER_ROOT}/${HORIZONS_REL}" \
      "${CONTAINER_ROOT}/${DEM_REL}" \
      "${CONTAINER_ROOT}/${OUTPUT_REL}"
    ;;

  *)
    echo "Unknown verb: ${VERB}" >&2
    usage
    exit 1
    ;;
esac
