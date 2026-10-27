param(
    [string]$ComfyRoot = $env:COMFYUI_ROOT,
    [string]$Image = 'tensor-master/pocket-h3:dev',
    [string]$LocalPython = $env:COMFYUI_PYTHON,
    [string]$CommunityLoraManifest = $env:COMMUNITY_LORA_MANIFEST,
    [switch]$BundleOnly
)

$ErrorActionPreference = 'Stop'
if (-not $ComfyRoot) { throw 'Pass -ComfyRoot or set COMFYUI_ROOT.' }
if (-not $LocalPython) { throw 'Pass -LocalPython or set COMFYUI_PYTHON.' }
$nodeRoot = (Resolve-Path -LiteralPath (Join-Path $ComfyRoot 'custom_nodes')).Path
$stageParent = Join-Path ([System.IO.Path]::GetTempPath()) 'TensorMasterH3Build'
New-Item -ItemType Directory -Path $stageParent -Force | Out-Null
$stage = Join-Path $stageParent ([guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage | Out-Null

$nodeFiles = [ordered]@{
    'Veda-on-ComfyUI' = @('__init__.py', 'veda_comfy', 'requirements.txt', 'pyproject.toml', 'LICENSE', 'NOTICE.md')
    'ComfyUI-TensorMaster-Preprocessors' = @('__init__.py', 'nodes.py', 'prepare.py', 'requirements.txt', 'models.lock.json', 'LICENSE', 'licenses', 'README.md', 'sync_models.py')
    'ComfyUI-Ref2VA-VSA' = @('__init__.py', 'nodes.py')
    'comfyui-krea2edit' = @('__init__.py')
    'h3_mobile' = @('__init__.py', 'web')
    'comfyui-kjnodes' = @('__init__.py', 'nodes', 'utility', 'fonts', 'kjweb_async', 'web', 'requirements.txt')
    'comfyui-minimaxrefpack' = @('__init__.py', 'minimax_refpack', 'web', 'requirements.txt')
    'ComfyUI-PromptDirector' = @('__init__.py', 'config.py', 'nodes.py', 'prompts.py', 'runtime.py', 'video.py', 'vlm_describe.py', 'system_prompts', 'web')
    'ComfyUI-H3-NativeAudioLock' = @('__init__.py')
    'comfyui-fizgig-h3-still' = @('__init__.py')
    'comfyui-videohelpersuite' = @('__init__.py', 'videohelpersuite', 'video_formats', 'web', 'requirements.txt')
    'comfyui-unload-model' = @('__init__.py', 'unloadModel.py')
    'comfyui-h3-facerefine' = @('__init__.py', 'nodes.py', 'picker_api.py', 'web')
    # ltx-hdr profile: LTX-2.5 SDR->HDR IC-LoRA nodes (local checkout incl. the optional-q8_kernels import patch)
    'ComfyUI-LTXVideo' = @('__init__.py', 'audio_only.py', 'conditioning_loader.py', 'conditioning_saver.py', 'decoder_noise.py',
        'dynamic_conditioning.py', 'easy_samplers.py', 'embeddings_connector.py', 'gemma_api_conditioning.py', 'gemma_configs',
        'gemma_encoder.py', 'guide.py', 'guiders', 'hdr_color.py', 'hdr_io.py', 'hdr_nodes.py', 'iclora.py', 'iclora_attention.py',
        'latent_norm.py', 'latents.py', 'looping_sampler.py', 'low_vram_loaders.py', 'masks.py', 'nodes_registry.py',
        'pos_embedding_exp_values.py', 'presets', 'prompt_enhancer_nodes.py', 'prompt_enhancer_utils.py', 'pyramid_blending.py',
        'q8_nodes.py', 'requirements.txt', 'rope_utils.py', 'sparse_tracks.py', 'stg.py', 'system_prompts',
        'text_embeddings_connectors.py', 'tiled_fusion_plan.py', 'tiled_fusion_sampler.py', 'tiled_fusion_sizes.py',
        'tiled_sampler.py', 'tiled_vae_decode.py', 'tricks', 'utiltily_nodes.py', 'vae_patcher.py', 'vanish_nodes.py', 'web')
    # ltx-hdr profile: RIFE x2 on the ACEScct ProRes (ltx-hdr-pipeline interp, farm mode)
    'comfyui-frame-interpolation' = @('__init__.py', 'other_nodes.py', 'vfi_utils.py', 'config.yaml', 'vfi_models')
}

try {
    $snapshot = Join-Path $stage 'runtime-snapshot'
    & $LocalPython (Join-Path $PSScriptRoot 'capture_local_runtime.py') --comfy-root $ComfyRoot --python $LocalPython --destination $snapshot
    if ($LASTEXITCODE -ne 0) { throw 'Could not capture the working local ComfyUI runtime' }
    $runtimeSnapshot = Get-Content -LiteralPath (Join-Path $snapshot 'local-runtime.json') -Raw | ConvertFrom-Json
    foreach ($node in $nodeFiles.Keys) {
        $destination = Join-Path $stage (Join-Path 'custom_nodes' $node)
        New-Item -ItemType Directory -Path $destination -Force | Out-Null
        foreach ($relative in $nodeFiles[$node]) {
            $source = Join-Path (Join-Path $nodeRoot $node) $relative
            if (-not (Test-Path -LiteralPath $source)) {
                throw "Missing Pocket H3 runtime source: $source"
            }
            Copy-Item -LiteralPath $source -Destination $destination -Recurse -Force
        }
    }
    $rifeCkpt = Join-Path $stage 'custom_nodes/comfyui-frame-interpolation/ckpts/rife'
    New-Item -ItemType Directory -Path $rifeCkpt -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $nodeRoot 'comfyui-frame-interpolation/ckpts/rife/rife49.pth') -Destination $rifeCkpt -Force
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'viggle_turbo.py') -Destination (Join-Path $stage 'custom_nodes\viggle_turbo.py') -Force
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'h3_beta57.py') -Destination (Join-Path $stage 'custom_nodes\h3_beta57.py') -Force
    if ($BundleOnly) {
        $runtimeDir = Join-Path $stage 'h3-runtime'
        $backendDir = Join-Path $stage 'backend'
        $appDir = Join-Path $backendDir 'app'
        New-Item -ItemType Directory -Path $runtimeDir, $appDir -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $snapshot 'local-runtime.json'), (Join-Path $snapshot 'local-runtime-constraints.txt') -Destination $runtimeDir
        foreach ($name in @('models.lock.json','sync_models.py','remote_prepare.sh','remote_sync.sh','remote_start.sh')) {
            Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $runtimeDir
        }
        $repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
        if (-not $CommunityLoraManifest) { $CommunityLoraManifest = Join-Path $repoRoot 'state\community-loras.json' }
        if (Test-Path -LiteralPath $CommunityLoraManifest) {
            Copy-Item -LiteralPath $CommunityLoraManifest -Destination (Join-Path $runtimeDir 'community-loras.json')
        }
        foreach ($name in @('__init__.py','worker.py','worker_recovery.py')) {
            Copy-Item -LiteralPath (Join-Path $repoRoot (Join-Path 'backend' $name)) -Destination $backendDir
        }
        foreach ($name in @('__init__.py','auth.py','config.py','credential_paths.py','h3_media.py')) {
            Copy-Item -LiteralPath (Join-Path $repoRoot (Join-Path 'backend\app' $name)) -Destination $appDir
        }
        $bundlePath = Join-Path $repoRoot 'state\h3-cloud-runtime.tar.gz'
        & tar -C $stage -czf $bundlePath .
        if ($LASTEXITCODE -ne 0) { throw "Cloud bundle failed with exit code $LASTEXITCODE" }
        Write-Output "Cloud bundle ready: $bundlePath"
    }
    else {
        & docker buildx build --load --build-context "nodes=$stage" --build-arg "COMFYUI_REF=$($runtimeSnapshot.comfyui_revision)" -t $Image $PSScriptRoot
        if ($LASTEXITCODE -ne 0) {
            throw "Docker build failed with exit code $LASTEXITCODE"
        }
    }
}
finally {
    $resolvedStage = [System.IO.Path]::GetFullPath($stage)
    $resolvedParent = [System.IO.Path]::GetFullPath($stageParent).TrimEnd('\', '/')
    if ($resolvedStage.StartsWith($resolvedParent + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $resolvedStage -Recurse -Force
    }
}
