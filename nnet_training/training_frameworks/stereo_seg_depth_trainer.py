#!/usr/bin/env python3

__author__ = "Bryce Ferenczi"
__email__ = "bryce.ferenczi@monashmotorsport.com"

import sys
import time
from pathlib import Path
from typing import Dict, TypeVar
T = TypeVar('T')
import numpy as np

import torch
import matplotlib.pyplot as plt

from nnet_training.utilities.metrics import SegmentationMetric, DepthMetric
from nnet_training.utilities.visualisation import get_color_pallete
from nnet_training.training_frameworks.trainer_base_class import ModelTrainer

__all__ = ['StereoSegDepthTrainer']

class StereoSegDepthTrainer(ModelTrainer):
    def __init__(self, model: torch.nn.Module, optim: torch.optim.Optimizer,
                 loss_fn: Dict[str, torch.nn.Module], lr_cfg: Dict[str, T],
                 dataldr: Dict[str, torch.utils.data.DataLoader],
                 modelpath: Path, checkpoints=True):
        '''
        Initialize the Model trainer giving it a nn.Model, nn.Optimizer and dataloaders as
        a dictionary with Training, Validation and Testing loaders
        '''
        self._seg_loss_fn = loss_fn['segmentation']
        self._depth_loss_fn = loss_fn['depth']

        self.metric_loggers = {
            'seg': SegmentationMetric(19, base_dir=modelpath, main_metric="IoU",
                                      savefile='segmentation_data'),
            'depth': DepthMetric(base_dir=modelpath, main_metric="RMSE_Log", savefile='depth_data')
        }

        super(StereoSegDepthTrainer, self).__init__(model, optim, dataldr, lr_cfg,
                                                    modelpath, checkpoints)

    def _train_epoch(self, max_epoch):
        self._model.train()

        start_time = time.time()

        for batch_idx, data in enumerate(self._training_loader):
            cur_lr = self._lr_manager(batch_idx)
            for param_group in self._optimizer.param_groups:
                param_group['lr'] = cur_lr

            # Put both image and target onto device
            left        = data['l_img'].to(self._device)
            right       = data['r_img'].to(self._device)
            seg_gt      = data['seg'].to(self._device)
            depth_gt    = data['disparity'].to(self._device)
            baseline    = data['cam']['baseline_T'].to(self._device)

            # Computer loss, use the optimizer object to zero all of the gradients
            # Then backpropagate and step the optimizer
            seg_pred, depth_pred = self._model(left, right)

            seg_loss = self._seg_loss_fn(seg_pred, seg_gt)
            l_depth_loss = self._depth_loss_fn(left, depth_pred, right, baseline, data['cam'])
            r_depth_loss = self._depth_loss_fn(right, depth_pred, left, -baseline, data['cam'])
            loss = seg_loss + l_depth_loss + r_depth_loss

            self._optimizer.zero_grad()
            loss.backward()
            self._optimizer.step()

            self.metric_loggers['seg'].add_sample(
                torch.argmax(seg_pred, dim=1, keepdim=True).cpu().data.numpy(),
                seg_gt.cpu().data.numpy(),
                loss=seg_loss.item()
            )

            self.metric_loggers['depth'].add_sample(
                depth_pred.cpu().data.numpy(),
                depth_gt.cpu().data.numpy(),
                loss=l_depth_loss.item() + r_depth_loss.item()
            )

            if not batch_idx % 10:
                time_elapsed = time.time() - start_time
                time_remain = time_elapsed / (batch_idx + 1) * (len(self._training_loader) - (batch_idx + 1))
                sys.stdout.flush()
                sys.stdout.write('\rTrain Epoch: [%2d/%2d] Iter [%4d/%4d] || lr: %.8f || Loss: %.4f || Time Elapsed: %.2f sec || Est Time Remain: %.2f sec' % (
                    self.epoch, max_epoch, batch_idx + 1, len(self._training_loader),
                    self._lr_manager.get_lr(), loss.item(), time_elapsed, time_remain))
        
    def _validate_model(self, max_epoch):
        with torch.no_grad():
            self._model.eval()

            start_time = time.time()

            for batch_idx, data in enumerate(self._validation_loader):
                # Put both image and target onto device
                left        = data['l_img'].to(self._device)
                right       = data['r_img'].to(self._device)
                seg_gt      = data['seg'].to(self._device)
                depth_gt    = data['disparity'].to(self._device)
                baseline    = data['cam']['baseline_T'].to(self._device)
           
                # Caculate the loss and accuracy for the predictions
                seg_pred, depth_pred = self._model(left, right)

                seg_loss = self._seg_loss_fn(seg_pred, seg_gt)
                l_depth_loss = self._depth_loss_fn(left, depth_pred, right, baseline, data['cam'])
                r_depth_loss = self._depth_loss_fn(right, depth_pred, left, -baseline, data['cam'])

                self.metric_loggers['seg'].add_sample(
                    torch.argmax(seg_pred, dim=1, keepdim=True).cpu().data.numpy(),
                    seg_gt.cpu().data.numpy(),
                    loss=seg_loss.item()
                )

                self.metric_loggers['depth'].add_sample(
                    depth_pred.cpu().data.numpy(),
                    depth_gt.cpu().data.numpy(),
                    loss=l_depth_loss.item() + r_depth_loss.item()
                )

                if not batch_idx % 10:
                    loss = seg_loss + l_depth_loss + r_depth_loss
                    seg_acc = self.metric_loggers['seg'].get_last_batch()
                    depth_acc = self.metric_loggers['depth'].get_last_batch()
                    time_elapsed = time.time() - start_time
                    time_remain = time_elapsed / (batch_idx + 1) * (len(self._validation_loader) - (batch_idx + 1))
                    sys.stdout.flush()
                    sys.stdout.write('\rValidaton Epoch: [%2d/%2d] Iter [%4d/%4d] || Depth Acc: %.4f || Seg Acc: %.4f || Loss: %.4f || Time Elapsed: %.2f sec || Est Time Remain: %.2f sec' % (
                            self.epoch, max_epoch, batch_idx + 1, len(self._validation_loader),
                            depth_acc, seg_acc, loss.item(), time_elapsed, time_remain))

    def visualize_output(self):
        """
        Forward pass over a testing batch and displays the output
        """
        with torch.no_grad():
            self._model.eval()
            data     = next(iter(self._validation_loader))
            left     = data['l_img'].to(self._device)
            right    = data['r_img'].to(self._device)
            seg_gt   = data['seg']
            depth_gt = data['disparity']

            start_time = time.time()

            seg_pred, depth_pred = self._model(left, right)
            seg_pred = torch.argmax(seg_pred, dim=1, keepdim=True)

            propagation_time = (time.time() - start_time)/self._validation_loader.batch_size

            for i in range(self._validation_loader.batch_size):
                plt.subplot(1,5,1)
                plt.imshow(np.moveaxis(left[i, 0:3, :, :].cpu().numpy(), 0, 2))
                plt.xlabel("Base Image")

                plt.subplot(1, 5, 2)
                plt.imshow(get_color_pallete(seg_gt[i,:,:]))
                plt.xlabel("Segmentation Ground Truth")

                plt.subplot(1, 5, 3)
                plt.imshow(get_color_pallete(seg_pred.cpu().numpy()[i, 0, :, :]))
                plt.xlabel("Segmentation Prediction")

                plt.subplot(1, 5, 4)
                plt.imshow(depth_gt[i, :, :])
                plt.xlabel("Depth Ground Truth")

                plt.subplot(1, 5, 5)
                plt.imshow(depth_pred.cpu().numpy()[i, 0, :, :])
                plt.xlabel("Depth Prediction")

                plt.suptitle("Propagation time: " + str(propagation_time))
                plt.show()

    def plot_data(self):
        super(StereoSegDepthTrainer, self).plot_data()
        self.metric_loggers['seg'].plot_classwise_iou()

if __name__ == "__main__":
    raise NotImplementedError
