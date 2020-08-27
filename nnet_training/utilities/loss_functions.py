"""Custom losses."""
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Variable

import numpy as np

__all__ = ['MixSoftmaxCrossEntropyLoss', 'MixSoftmaxCrossEntropyOHEMLoss', 'FocalLoss2D',
    'DepthAwareLoss', 'ScaleInvariantError', 'InvHuberLoss', 'ReconstructionLossV1',
    'ReconstructionLossV2']

### MixSoftmaxCrossEntropyLoss etc from F-SCNN Repo
class MixSoftmaxCrossEntropyLoss(nn.CrossEntropyLoss):
    def __init__(self, aux=True, aux_weight=0.2, ignore_label=-1, **kwargs):
        super(MixSoftmaxCrossEntropyLoss, self).__init__(ignore_index=ignore_label)
        self.aux = aux
        self.aux_weight = aux_weight

    def _aux_forward(self, *inputs, **kwargs):
        *preds, target = tuple(inputs)

        loss = super(MixSoftmaxCrossEntropyLoss, self).forward(preds[0], target)
        for i in range(1, len(preds)):
            aux_loss = super(MixSoftmaxCrossEntropyLoss, self).forward(preds[i], target)
            loss += self.aux_weight * aux_loss
        return loss

    def forward(self, *inputs, **kwargs):
        preds, target = tuple(inputs)
        inputs = tuple(list(preds) + [target])
        if self.aux:
            return self._aux_forward(*inputs)
        else:
            return super(MixSoftmaxCrossEntropyLoss, self).forward(*inputs)


class SoftmaxCrossEntropyOHEMLoss(nn.Module):
    def __init__(self, ignore_label=-1, thresh=0.7, min_kept=256, use_weight=True, **kwargs):
        super(SoftmaxCrossEntropyOHEMLoss, self).__init__()
        self.ignore_label = ignore_label
        self.thresh = float(thresh)
        self.min_kept = int(min_kept)
        if use_weight:
            print("w/ class balance")
            weight = torch.FloatTensor([0.8373, 0.918, 0.866, 1.0345, 1.0166, 0.9969, 0.9754,
                                        1.0489, 0.8786, 1.0023, 0.9539, 0.9843, 1.1116, 0.9037, 1.0865, 1.0955,
                                        1.0865, 1.1529, 1.0507])
            self.criterion = torch.nn.CrossEntropyLoss(weight=weight, ignore_index=ignore_label)
        else:
            print("w/o class balance")
            self.criterion = torch.nn.CrossEntropyLoss(ignore_index=ignore_label)

    def forward(self, predict, target, weight=None):
        assert not target.requires_grad
        assert predict.dim() == 4
        assert target.dim() == 3
        assert predict.size(0) == target.size(0), "{0} vs {1} ".format(predict.size(0), target.size(0))
        assert predict.size(2) == target.size(1), "{0} vs {1} ".format(predict.size(2), target.size(1))
        assert predict.size(3) == target.size(2), "{0} vs {1} ".format(predict.size(3), target.size(3))

        n, c, h, w = predict.size()
        input_label = target.data.cpu().numpy().ravel().astype(np.int32)
        x = np.rollaxis(predict.data.cpu().numpy(), 1).reshape((c, -1))
        input_prob = np.exp(x - x.max(axis=0).reshape((1, -1)))
        input_prob /= input_prob.sum(axis=0).reshape((1, -1))

        valid_flag = input_label != self.ignore_label
        valid_inds = np.where(valid_flag)[0]
        label = input_label[valid_flag]
        num_valid = valid_flag.sum()
        if self.min_kept >= num_valid:
            print('Labels: {}'.format(num_valid))
        elif num_valid > 0:
            prob = input_prob[:, valid_flag]
            pred = prob[label, np.arange(len(label), dtype=np.int32)]
            threshold = self.thresh
            if self.min_kept > 0:
                index = pred.argsort()
                threshold_index = index[min(len(index), self.min_kept) - 1]
                if pred[threshold_index] > self.thresh:
                    threshold = pred[threshold_index]
            kept_flag = pred <= threshold
            valid_inds = valid_inds[kept_flag]

        label = input_label[valid_inds].copy()
        input_label.fill(self.ignore_label)
        input_label[valid_inds] = label
        valid_flag_new = input_label != self.ignore_label
        # print(np.sum(valid_flag_new))
        target = Variable(torch.from_numpy(input_label.reshape(target.size())).long().cuda())

        return self.criterion(predict, target)


class MixSoftmaxCrossEntropyOHEMLoss(SoftmaxCrossEntropyOHEMLoss):
    def __init__(self, aux=False, aux_weight=0.2, ignore_index=-1, **kwargs):
        super(MixSoftmaxCrossEntropyOHEMLoss, self).__init__(ignore_label=ignore_index, **kwargs)
        self.aux = aux
        self.aux_weight = aux_weight

    def _aux_forward(self, *inputs, **kwargs):
        *preds, target = tuple(inputs)

        loss = super(MixSoftmaxCrossEntropyOHEMLoss, self).forward(preds[0], target)
        for i in range(1, len(preds)):
            aux_loss = super(MixSoftmaxCrossEntropyOHEMLoss, self).forward(preds[i], target)
            loss += self.aux_weight * aux_loss
        return loss

    def forward(self, *inputs, **kwargs):
        preds, target = tuple(inputs)
        inputs = tuple(list([preds]) + [target])
        if self.aux:
            return self._aux_forward(*inputs)
        else:
            return super(MixSoftmaxCrossEntropyOHEMLoss, self).forward(*inputs)

class FocalLoss2D(nn.Module):
    """
    https://github.com/doiken23/focal_segmentation/blob/master/focalloss2d.py
    OG Source but I've modified a bit
    """
    def __init__(self, gamma=0, weight=None, size_average=True, ignore_index=-100):
        super(FocalLoss2D, self).__init__()

        self.gamma = gamma
        self.weight = weight
        self.size_average = size_average
        self._ignore_index = ignore_index

    def forward(self, input, target):
        if input.dim()>2:
            input = input.contiguous().view(input.size(0), input.size(1), -1)
            input = input.transpose(1,2)
            input = input.contiguous().view(-1, input.size(2)).squeeze()
        if target.dim()==4:
            target = target.contiguous().view(target.size(0), target.size(1), -1)
            target = target.transpose(1,2)
            target = target.contiguous().view(-1, target.size(2)).squeeze()
        elif target.dim()==3:
            target = target.view(-1)
        else:
            target = target.view(-1, 1)

        # compute the negative likelyhood
        # weight = Variable(self.weight)
        logpt = -F.cross_entropy(input, target, ignore_index=self._ignore_index)
        pt = torch.exp(logpt)

        # compute the loss
        loss = -((1-pt)**self.gamma) * logpt

        # averaging (or not) loss
        if self.size_average:
            return loss.mean()
        else:
            return loss.sum()

class DepthAwareLoss(nn.Module):
    def __init__(self, size_average=True, ignore_index=0):
        super(DepthAwareLoss, self).__init__()
        self.size_average = size_average
        self._ignore_index = ignore_index

    def forward(self, pred, target):
        pred = pred.squeeze(dim=1)
        regularization = 1 - torch.min(torch.log(pred), torch.log(target)) / torch.max(torch.log(pred), torch.log(target))
        l_loss = F.smooth_l1_loss(pred, target ,size_average=self.size_average)
        depth_aware_attention = target / torch.max(target)
        return ((depth_aware_attention + regularization)*l_loss).mean()

class ScaleInvariantError(nn.Module):
    def __init__(self, lmda=1, ignore_index=-1):
        super(ScaleInvariantError, self).__init__()
        self.lmda = lmda
        self._ignore_index = ignore_index

    def forward(self, pred, target):
        #   Number of pixels per image
        n_pixels = target.shape[1]*target.shape[2]
        #   Number of valid pixels in target image
        n_valid = (target != self._ignore_index).view(-1, n_pixels).float().sum(dim=1)

        #   Prevent infs and nans
        pred[pred<=0] = 0.00001
        pred = pred.squeeze(dim=1)
        pred[target==self._ignore_index] = 0.00001
        target[target==self._ignore_index] = 0.00001
        d = torch.log(pred) - torch.log(target)

        element_wise = torch.pow(d.view(-1, n_pixels),2).sum(dim=1)/n_valid
        scaled_error = self.lmda*(torch.pow(d.view(-1, n_pixels).sum(dim=1),2)/(n_valid**2))
        return (element_wise - scaled_error).mean()

class InvHuberLoss(nn.Module):
    def __init__(self, ignore_index=-1):
        super(InvHuberLoss, self).__init__()
        self.ignore_index = ignore_index
    
    def forward(self, pred, target):
        pred_relu = F.relu(pred.squeeze(dim=1)) # depth predictions must be >=0
        diff = pred_relu - target
        mask = target != self.ignore_index

        err = (diff * mask.float()).abs()
        c = 0.2 * err.max()
        err2 = (diff**2 + c**2) / (2. * c)
        mask_err = err <= c
        mask_err2 = err > c
        cost = (err*mask_err.float() + err2*mask_err2.float()).mean()
        return cost

class ReconstructionLossV1(nn.Module):
    def __init__(self, img_b, img_h, img_w, device=torch.device("cpu")):
        super(ReconstructionLossV1, self).__init__()
        self.img_h = img_h
        self.img_w = img_w

        base_x = torch.arange(0, img_w).repeat(img_b, img_h, 1)/float(img_w)*2.-1.
        base_y = torch.arange(0, img_h).repeat(img_b, img_w, 1).transpose(1, 2)/float(img_h)*2.-1.
        self.transf_base = torch.stack([base_x, base_y], 3).to(device)
    
    def forward(self, source, flow, target):
        flow = flow.reshape(-1, self.img_h, self.img_w, 2)
        flow[:,:,:,0] /= self.img_w
        flow[:,:,:,1] /= self.img_h
        flow += self.transf_base
        pred = F.grid_sample(source, flow, mode='bilinear', padding_mode='zeros', align_corners=None)

        # debug disp
        # import matplotlib.pyplot as plt
        # plt.subplot(1,2,1)
        # plt.imshow(np.moveaxis(source[0,0:3,:,:].cpu().numpy(),0,2))
        # plt.subplot(1,2,2)
        # plt.imshow(np.moveaxis(pred[0,0:3,:,:].cpu().numpy(),0,2))
        # plt.show()

        diff = (target-pred).abs()
        loss = diff.view([1,-1]).sum(1).mean() / (self.img_w * self.img_h)
        return loss

class SSIM(nn.Module):
    """Layer to compute the SSIM loss between a pair of images
    """
    def __init__(self):
        super(SSIM, self).__init__()
        self.mu_x_pool   = nn.AvgPool2d(3, 1)
        self.mu_y_pool   = nn.AvgPool2d(3, 1)
        self.sig_x_pool  = nn.AvgPool2d(3, 1)
        self.sig_y_pool  = nn.AvgPool2d(3, 1)
        self.sig_xy_pool = nn.AvgPool2d(3, 1)

        self.refl = nn.ReflectionPad2d(1)

        self.C1 = 0.01 ** 2
        self.C2 = 0.03 ** 2

    def forward(self, x, y):
        x = self.refl(x)
        y = self.refl(y)

        mu_x = self.mu_x_pool(x)
        mu_y = self.mu_y_pool(y)

        sigma_x  = self.sig_x_pool(x ** 2) - mu_x ** 2
        sigma_y  = self.sig_y_pool(y ** 2) - mu_y ** 2
        sigma_xy = self.sig_xy_pool(x * y) - mu_x * mu_y

        SSIM_n = (2 * mu_x * mu_y + self.C1) * (2 * sigma_xy + self.C2)
        SSIM_d = (mu_x ** 2 + mu_y ** 2 + self.C1) * (sigma_x + sigma_y + self.C2)

        return torch.clamp((1 - SSIM_n / SSIM_d) / 2, 0, 1)

class BackprojectDepth(nn.Module):
    """Layer to transform a depth image into a point cloud
    """
    def __init__(self, batch_size, height, width):
        super(BackprojectDepth, self).__init__()

        self.batch_size = batch_size
        self.height = height
        self.width = width

        meshgrid = np.meshgrid(range(self.width), range(self.height), indexing='xy')
        self.id_coords = np.stack(meshgrid, axis=0).astype(np.float32)
        self.id_coords = nn.Parameter(torch.from_numpy(self.id_coords),
                                      requires_grad=False)

        self.ones = nn.Parameter(torch.ones(self.batch_size, 1, self.height * self.width),
                                 requires_grad=False)

        self.pix_coords = torch.unsqueeze(torch.stack(
            [self.id_coords[0].view(-1), self.id_coords[1].view(-1)], 0), 0)
        self.pix_coords = self.pix_coords.repeat(batch_size, 1, 1)
        self.pix_coords = nn.Parameter(torch.cat([self.pix_coords, self.ones], 1),
                                       requires_grad=False)

    def forward(self, depth, inv_K):
        cam_points = torch.matmul(inv_K[:, :3, :3].cuda(), self.pix_coords)
        cam_points = depth.view(self.batch_size, 1, -1) * cam_points
        cam_points = torch.cat([cam_points, self.ones], 1)

        return cam_points

class Project3D(nn.Module):
    """Layer which projects 3D points into a camera with intrinsics K and at position T
    """
    def __init__(self, batch_size, height, width, eps=1e-7):
        super(Project3D, self).__init__()

        self.batch_size = batch_size
        self.height = height
        self.width = width
        self.eps = eps

    def forward(self, points, K, T):
        P = torch.matmul(K.cuda(), T.cuda())[:, :3, :]

        cam_points = torch.matmul(P, points)

        pix_coords = cam_points[:, :2, :] / (cam_points[:, 2, :].unsqueeze(1) + self.eps)
        pix_coords = pix_coords.view(self.batch_size, 2, self.height, self.width)
        pix_coords = pix_coords.permute(0, 2, 3, 1)
        pix_coords[..., 0] /= self.width - 1
        pix_coords[..., 1] /= self.height - 1
        pix_coords = (pix_coords - 0.5) * 2
        return pix_coords

class ReconstructionLossV2(nn.Module):
    """Generate the warped (reprojected) color images for a minibatch.
    """
    def __init__(self, batch_size, height, width, pred_type="disparity", ssim=True):
        super(ReconstructionLossV2, self).__init__()
        self.pred_type      = pred_type
        self.BackprojDepth  = BackprojectDepth(batch_size, height, width)\
                                .to("cuda" if torch.cuda.is_available() else "cpu")
        self.Project3D      = Project3D(batch_size, height, width)\
                                .to("cuda" if torch.cuda.is_available() else "cpu")
        if ssim:
            self.SSIM       = SSIM().to("cuda" if torch.cuda.is_available() else "cpu")
    
    def depth_from_disparity(self, disparity):
        return (0.209313 * 2262.52) / ((disparity - 1) / 256)
    
    def forward(self, source_img, prediction, target_img, telemetry, camera):
        if self.pred_type is "depth":
            depth = prediction
        elif self.pred_type is "disparity":
            depth = self.depth_from_disparity(prediction)
        elif self.pred_type is "flow":
            raise NotImplementedError

        cam_points = self.BackprojDepth(depth, camera["inv_K"])
        pix_coords = self.Project3D(cam_points, camera["K"], telemetry)

        source_img = F.grid_sample(source_img, pix_coords, padding_mode="border")

        abs_diff = (target_img - source_img).abs()
        if hasattr(self, 'SSIM'):
            loss = 0.15*abs_diff.mean(1, True) + 0.85*self.SSIM(source_img, target_img).mean(1, True)
        else:
            loss = abs_diff.mean(1, True)
        return loss.mean()

if __name__ == '__main__':
    import PIL.Image as Image
    import torchvision.transforms

    img1 = Image.open('/media/bryce/4TB Seagate/Autonomous Vehicles Data/Cityscapes Data/leftImg8bit/test/berlin/berlin_000000_000019_leftImg8bit.png')
    img2 = Image.open('/media/bryce/4TB Seagate/Autonomous Vehicles Data/Cityscapes Data/leftImg8bit_sequence/test/berlin/berlin_000000_000020_leftImg8bit.png')

    loss_fn = ReconstructionLossV1(1, img1.size[1], img1.size[0])
    uniform = torch.zeros([1,img1.size[1],img1.size[0],2])
    
    transform = torchvision.transforms.ToTensor()
    loss = loss_fn(transform(img1).unsqueeze(0), uniform, transform(img1).unsqueeze(0))
    print(loss)