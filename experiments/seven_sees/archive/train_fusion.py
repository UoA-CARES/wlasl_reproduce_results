import os
import torch
import torch.nn as nn
import wandb
import numpy as np

from dataset.dataset import MultiModalDataset
from mmcv_model.mmcv_csn import ResNet3dCSN
from mmcv_model.scheduler import GradualWarmupScheduler

from model.multimodal_neck import MultiModalNeck
from model.simple_head import SimpleHead
from model.multimodal_model import MultiModalModel


def top_k_accuracy(scores, labels, topk=(1, )):
    """Calculate top k accuracy score.
    Args:
        scores (list[np.ndarray]): Prediction scores for each class.
        labels (list[int]): Ground truth labels.
        topk (tuple[int]): K value for top_k_accuracy. Default: (1, ).
    Returns:
        list[float]: Top k accuracy score for each k.
    """
    res = np.zeros(len(topk))
    labels = np.array(labels)[:, np.newaxis]
    for i, k in enumerate(topk):
        max_k_preds = np.argsort(scores, axis=1)[:, -k:][:, ::-1]
        match_array = np.logical_or.reduce(max_k_preds == labels, axis=1)
        topk_acc_score = match_array.sum() / match_array.shape[0]
        res[i] = topk_acc_score

    return res


def train_one_epoch(epoch_index, interval=5):
    """Run one epoch for training.
    Args:
        epoch_index (int): Current epoch.
        interval (int): Frequency at which to print logs.
    Returns:
        last_loss (float): Loss value for the last batch.
    """
    running_loss = 0.
    last_loss = 0.

    # Here, we use enumerate(training_loader) instead of
    # iter(training_loader) so that we can track the batch
    # index and do some intra-epoch reporting
    for i, results in enumerate(train_loader):
        rgb = results['rgb']
        flow = results['flow']
        depth = results['depth']
        face = results['face']
        skeleton = results['skeleton']
        left_hand = results['left_hand']
        right_hand = results['right_hand']
        targets = results['label']
        targets = targets.reshape(-1, )

        rgb, flow, depth, face, skeleton, left_hand, right_hand, targets = rgb.to(device), flow.to(device), depth.to(device), face.to(device), skeleton.to(device), left_hand.to(device), right_hand.to(device), targets.to(device)

        # Zero your gradients for every batch!
        optimizer.zero_grad()

        # Make predictions for this batch
        outputs = model(rgb=rgb, 
                        flow=flow,
                        depth=depth,
                        left_hand=left_hand,
                        right_hand=right_hand,
                        face=face,
                        skeleton=skeleton)

        # Compute the loss and its gradients
        loss = loss_fn(outputs, targets)
        loss.backward()

        # Gradient Clipping
        torch.nn.utils.clip_grad_norm_(
            model.parameters(), max_norm=40, norm_type=2.0)

        # Adjust learning weights
        optimizer.step()

        # Gather data and report
        running_loss += loss.item()
        if i % interval == interval-1:
            last_loss = running_loss / interval  # loss per batch
            print(
                f'Epoch [{epoch_index}][{i+1}/{len(train_loader)}], lr: {scheduler.get_last_lr()[0]:.5e}, loss: {last_loss:.5}')
            running_loss = 0.

    return last_loss, scheduler.get_last_lr()[0]


def validate():
    """Run one epoch for validation.
    Returns:
        avg_vloss (float): Validation loss value for the last batch.
        top1_acc (float): Top-1 accuracy in decimal.
        top5_acc (float): Top-5 accuracy in decimal.
    """
    running_vloss = 0.0
    running_vacc = np.zeros(2)

    print('Evaluating top_k_accuracy...')

    model.eval()
    with torch.inference_mode():
        for i, results in enumerate(test_loader):

            rgb = results['rgb']
            flow = results['flow']
            depth = results['depth']
            face = results['face']
            skeleton = results['skeleton']
            left_hand = results['left_hand']
            right_hand = results['right_hand']
            vtargets = results['label']
            vtargets = vtargets.reshape(-1, )

            rgb, flow, depth, face, skeleton, left_hand, right_hand, vtargets = rgb.to(device), flow.to(device), depth.to(device), face.to(device), skeleton.to(device), left_hand.to(device), right_hand.to(device), vtargets.to(device)

            # Zero your gradients for every batch!
            optimizer.zero_grad()

            # Make predictions for this batch
            voutputs = model(rgb=rgb, 
                            flow=flow,
                            depth=depth,
                            left_hand=left_hand,
                            right_hand=right_hand,
                            face=face,
                            skeleton=skeleton)

            vloss = loss_fn(voutputs, vtargets)
            running_vloss += vloss

            running_vacc += top_k_accuracy(voutputs.detach().cpu().numpy(),
                                           vtargets.detach().cpu().numpy(), topk=(1, 5))

    avg_vloss = running_vloss / (i + 1)

    acc = running_vacc/len(test_loader)
    top1_acc = acc[0].item()
    top5_acc = acc[1].item()

    return (avg_vloss, top1_acc, top5_acc)


if __name__ == '__main__':
    print('Loading rgb backbone checkpoint...')
    rgb_checkpoint = torch.load('rgb_backbone.pth')
    print('Loading flow backbone checkpoint...')
    flow_checkpoint = torch.load('flow_backbone.pth')
    print('Loading depth backbone checkpoint...')
    depth_checkpoint = torch.load('depth_backbone.pth')
    print('Loading face backbone checkpoint...')
    face_checkpoint = torch.load('face_backbone.pth')
    print('Loading right_hand backbone checkpoint...')
    right_hand_checkpoint = torch.load('right_hand_backbone.pth')
    print('Loading left_hand backbone checkpoint...')
    left_hand_checkpoint = torch.load('left_hand_backbone.pth')
    print('Loading skeleton backbone checkpoint...')
    skeleton_checkpoint = torch.load('skeleton_backbone.pth')

    os.chdir('../../..')

    wandb.init(entity="cares", project="jack-slr",
               group="fusion", name="all-one-fc")

    # Set up device agnostic code
    device = 'cuda'

    # Configs
    work_dir = 'work_dirs/7sees-late-fusion/'
    batch_size = 10

    os.makedirs(work_dir, exist_ok=True)

    train_dataset = MultiModalDataset(ann_file='data/wlasl/train_annotations.txt',
                                      root_dir='data/wlasl/rawframes',
                                      clip_len=32,
                                      modalities=('rgb', 'flow', 'skeleton', 'pose', 'depth', 'face', 'left_hand', 'right_hand'),
                                      resolution=224,
                                      frame_interval=1,
                                      input_resolution=256,
                                      num_clips=1
                                      )

    test_dataset = MultiModalDataset(ann_file='data/wlasl/test_annotations.txt',
                                     root_dir='data/wlasl/rawframes',
                                     clip_len=32,
                                     resolution=224,
                                     modalities=('rgb', 'flow', 'skeleton', 'pose', 'depth', 'face', 'left_hand', 'right_hand'),
                                     test_mode=True,
                                     frame_interval=1,
                                     input_resolution=256,
                                     num_clips=1
                                     )


    # Setting up dataloaders
    train_loader = torch.utils.data.DataLoader(dataset=train_dataset,
                                               batch_size=batch_size,
                                               shuffle=True,
                                               num_workers=4,
                                               pin_memory=True)

    test_loader = torch.utils.data.DataLoader(dataset=test_dataset,
                                              batch_size=1,
                                              shuffle=True,
                                              num_workers=4,
                                              pin_memory=True)

    # Custom multimodal model
    rgb_backbone = ResNet3dCSN(
        pretrained2d=False,
        # pretrained=None,
        pretrained='https://download.openmmlab.com/mmaction/recognition/csn/ircsn_from_scratch_r50_ig65m_20210617-ce545a37.pth',
        depth=50,
        with_pool2=False,
        bottleneck_mode='ir',
        norm_eval=True,
        zero_init_residual=False,
        bn_frozen=True
    )

    rgb_backbone.load_state_dict(rgb_checkpoint)
    del rgb_checkpoint

    # Custom multimodal model
    depth_backbone = ResNet3dCSN(
        pretrained2d=False,
        # pretrained=None,
        pretrained='https://download.openmmlab.com/mmaction/recognition/csn/ircsn_from_scratch_r50_ig65m_20210617-ce545a37.pth',
        depth=50,
        with_pool2=False,
        bottleneck_mode='ir',
        norm_eval=True,
        zero_init_residual=False,
        bn_frozen=True
    )

    depth_backbone.load_state_dict(depth_checkpoint)
    del depth_checkpoint

    # Custom multimodal model
    face_backbone = ResNet3dCSN(
        pretrained2d=False,
        # pretrained=None,
        pretrained='https://download.openmmlab.com/mmaction/recognition/csn/ircsn_from_scratch_r50_ig65m_20210617-ce545a37.pth',
        depth=50,
        with_pool2=False,
        bottleneck_mode='ir',
        norm_eval=True,
        zero_init_residual=False,
        bn_frozen=True
    )

    face_backbone.load_state_dict(face_checkpoint)
    del face_checkpoint

    # Custom multimodal model
    left_hand_backbone = ResNet3dCSN(
        pretrained2d=False,
        # pretrained=None,
        pretrained='https://download.openmmlab.com/mmaction/recognition/csn/ircsn_from_scratch_r50_ig65m_20210617-ce545a37.pth',
        depth=50,
        with_pool2=False,
        bottleneck_mode='ir',
        norm_eval=True,
        zero_init_residual=False,
        bn_frozen=True
    )

    left_hand_backbone.load_state_dict(left_hand_checkpoint)
    del left_hand_checkpoint


    # Custom multimodal model
    right_hand_backbone = ResNet3dCSN(
        pretrained2d=False,
        # pretrained=None,
        pretrained='https://download.openmmlab.com/mmaction/recognition/csn/ircsn_from_scratch_r50_ig65m_20210617-ce545a37.pth',
        depth=50,
        with_pool2=False,
        bottleneck_mode='ir',
        norm_eval=True,
        zero_init_residual=False,
        bn_frozen=True
    )

    right_hand_backbone.load_state_dict(right_hand_checkpoint)
    del right_hand_checkpoint

    # Custom multimodal model
    skeleton_backbone = ResNet3dCSN(
        pretrained2d=False,
        # pretrained=None,
        pretrained='https://download.openmmlab.com/mmaction/recognition/csn/ircsn_from_scratch_r50_ig65m_20210617-ce545a37.pth',
        depth=50,
        with_pool2=False,
        bottleneck_mode='ir',
        norm_eval=True,
        zero_init_residual=False,
        bn_frozen=True
    )

    skeleton_backbone.load_state_dict(skeleton_checkpoint)
    del skeleton_checkpoint

    flow_backbone = ResNet3dCSN(
        pretrained2d=False,
        # pretrained=None,
        pretrained='https://download.openmmlab.com/mmaction/recognition/csn/ircsn_from_scratch_r50_ig65m_20210617-ce545a37.pth',
        depth=50,
        with_pool2=False,
        bottleneck_mode='ir',
        norm_eval=True,
        zero_init_residual=False,
        bn_frozen=True
    )

    # flow_backbone.init_weights()

    flow_backbone.load_state_dict(flow_checkpoint)
    del flow_checkpoint

    print('Backbones loaded successfully.')

    # Freeze the backbones
    for name, para in rgb_backbone.named_parameters():
        para.requires_grad = False

    for name, para in flow_backbone.named_parameters():
        para.requires_grad = False

    for name, para in depth_backbone.named_parameters():
        para.requires_grad = False

    for name, para in skeleton_backbone.named_parameters():
        para.requires_grad = False

    for name, para in right_hand_backbone.named_parameters():
        para.requires_grad = False

    for name, para in left_hand_backbone.named_parameters():
        para.requires_grad = False

    for name, para in skeleton_backbone.named_parameters():
        para.requires_grad = False


    neck = MultiModalNeck()
    head = SimpleHead(num_classes=400,
                      in_channels=2048*7,
                      dropout_ratio=0.5,
                      init_std=0.01)

    head.init_weights()

    model = MultiModalModel(rgb_backbone=rgb_backbone,
                            flow_backbone=flow_backbone,
                            depth_backbone=depth_backbone,
                            right_hand_backbone=right_hand_backbone,
                            left_hand_backbone=left_hand_backbone,
                            face_backbone=face_backbone,
                            skeleton_backbone=skeleton_backbone,
                            neck=neck,
                            head=head)

    # # Load model checkpoint
    # checkpoint = torch.load(work_dir+'latest.pth')
    # model.load_state_dict(checkpoint)

    # Specify optimizer
    optimizer = torch.optim.SGD(
        model.parameters(), lr=0.000125, momentum=0.9, weight_decay=0.00001)

    # Specify Loss
    loss_cls = nn.CrossEntropyLoss()

    # Specify total epochs
    epochs = 100

    # Specify learning rate scheduler
    lr_scheduler = torch.optim.lr_scheduler.StepLR(
        optimizer, step_size=120, gamma=0.1)

    scheduler_steplr = torch.optim.lr_scheduler.MultiStepLR(
        optimizer, milestones=[34, 84], gamma=0.1)
    scheduler = GradualWarmupScheduler(
        optimizer, multiplier=1, total_epoch=16, after_scheduler=scheduler_steplr)

    # Specify Loss
    loss_fn = nn.CrossEntropyLoss()

    # Setup wandb
    wandb.watch(model, log_freq=10)

    # Train Loop

    # Transfer model to device
    model.to(device)

    for epoch in range(epochs):
        # Turn on gradient tracking and do a forward pass
        model.train(True)
        avg_loss, learning_rate = train_one_epoch(epoch+1)

        # Turn off  gradients for reporting
        model.train(False)

        avg_vloss, top1_acc, top5_acc = validate()

        print(
            f'top1_acc: {top1_acc:.4}, top5_acc: {top5_acc:.4}, train_loss: {avg_loss:.5}, val_loss: {avg_vloss:.5}')

        # Track best performance, and save the model's state
        model_path = work_dir + f'epoch_{epoch+1}.pth'
        print(f'Saving checkpoint at {epoch+1} epochs...')
        torch.save(model.state_dict(), model_path)

        # Adjust learning rate
        scheduler.step()

        # Track wandb
        wandb.log({'train/loss': avg_loss,
                   'train/learning_rate': learning_rate,
                   'val/loss': avg_vloss,
                   'val/top1_accuracy': top1_acc,
                   'val/top5_accuracy': top5_acc})
