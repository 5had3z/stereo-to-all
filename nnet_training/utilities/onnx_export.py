#!/usr/bin/env python3

__author__ = "Bryce Ferenczi"
__email__ = "bryce.ferenczi@monashmotorsport.com"

import json
import hashlib
import argparse
from pathlib import Path
from easydict import EasyDict

import torch

from nnet_training.nnet_models import get_model

def correlation_op(g, input1, input2, pad_size, kernel_size,
                   max_displacement, stride1, stride2, corr_multiply):
    return g.op("cerberus::correlation", input1, input2, pad_size,
                kernel_size, max_displacement, stride1, stride2, corr_multiply)

def grid_sample_op(g, input1, input2, mode, padding_mode, align_corners):
    return g.op("cerberus::grid_sample", input1, input2, mode, padding_mode, align_corners)

if __name__ == "__main__":
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--config', default='configs/HRNetV2_sfd_kt.json')
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = EasyDict(json.load(f))

    encoding = hashlib.md5(json.dumps(cfg).encode('utf-8'))
    experiment_path = Path.cwd() / "torch_models" / str(encoding.hexdigest())

    print("Loading Model")
    model = get_model(cfg.model).to(device)
    model.eval()
    modelweights = experiment_path / (str(model)+"_latest.pth")
    checkpoint = torch.load(modelweights, map_location=device)
    model.load_state_dict(checkpoint['model_state_dict'])

    torch.onnx.register_custom_op_symbolic('cerberus::correlation', correlation_op, 11)
    # torch.onnx.register_custom_op_symbolic('torch::grid_sampler', grid_sample_op, 11)

    dim_h = cfg.dataset.augmentations.output_size[0]
    dim_w = cfg.dataset.augmentations.output_size[1]
    dummy_input_1 = torch.randn(1, 3, dim_h, dim_w, device=device)
    dummy_input_2 = torch.randn(1, 3, dim_h, dim_w, device=device)

    print("Exporting ONNX Engine")
    torch.onnx.export(
        model, (dummy_input_1, dummy_input_2),
        "onnx_models/export_test.onnx",
        opset_version=11, verbose=True)
