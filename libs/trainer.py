#!/usr/bin/python3
# -*- coding: utf-8 -*-

from logging import getLogger

import torch
import torch.nn as nn
from torch import optim
from utils.common import CommonUtils
from utils.optimizers import Optimizers

logger = getLogger('DLISE')

class Trainer(object):

    def __init__(self, model, device, config, save_dir):
        
        self.model = model
        self.device = device
        self.config = config
        self.save_dir = save_dir

    def run(self, train_loader, validate_loader):

        loss_fn =nn.MSELoss()

        optimizer = Optimizers.get_optimizer(self.config.train.optimizer, self.model.parameters())
        scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, self.config.train.optimizer.T_max)
        
        logger.info('Begin training')
        for epoch in range(1, self.config.train.epoch+1):

            enable_scheduler = (epoch > self.config.train.optimizer.wait_decay_epoch)
            if epoch == self.config.train.optimizer.wait_decay_epoch + 1:
                logger.info(f'Enable learning rate scheduler at Epoch: {epoch:05}')

            # Warm restart
            if enable_scheduler and (epoch % self.config.train.optimizer.T_max == 1):
                for param_group in optimizer.param_groups:
                    param_group['lr'] = self.config.train.optimizer.lr

            train_loss = self._train(loss_fn, optimizer, train_loader)
            val_loss = self._validate(loss_fn, validate_loader)

            if enable_scheduler:
                scheduler.step()

            logger.info(f'Epoch [{epoch:05}/{self.config.train.epoch:05}], Loss: {train_loss:.5f}, Val Loss: {val_loss:.5f}')

            if epoch % self.config.train.weight_save_period == 0:
                save_path = self.save_dir.joinpath('weights', f'weight-{str(epoch).zfill(5)}_{train_loss:.5f}_{val_loss:.5f}.pth')
                CommonUtils.save_weight(self.model, save_path)
                logger.info(f'Saved weight at Epoch : {epoch:05}')


    def _train(self, loss_fn, optimizer, train_loader):

        # Keep track of training loss
        train_loss = 0.

        # Train the model in each mini-batch
        self.model.train()
        for mini_batch in train_loader:

            # Send data to GPU dvice
            input_lats = mini_batch[0].to(self.device)
            input_lons = mini_batch[1].to(self.device)
            input_maps = mini_batch[2].to(self.device)
            targets = mini_batch[3].to(self.device)

            # Forward
            optimizer.zero_grad()
            outputs = self.model(input_lats, input_lons, input_maps)
            loss = loss_fn(outputs, targets)

            # Backward and update weights
            loss.backward()
            optimizer.step()

            # Update training loss
            train_loss += loss.item()

        train_loss /= len(train_loader.dataset)

        return train_loss


    def _validate(self, loss_fn, valid_loader):

        # Keep track of validation loss
        valid_loss = 0.0

        # Not use gradient for inference
        self.model.eval()
        with torch.no_grad():

            # Validate in each mini-batch
            for mini_batch in valid_loader:

                # Send data to GPU dvice
                input_lats = mini_batch[0].to(self.device)
                input_lons = mini_batch[1].to(self.device)
                input_maps = mini_batch[2].to(self.device)
                targets = mini_batch[3].to(self.device)

                # Forward
                outputs = self.model(input_lats, input_lons, input_maps)
                loss = loss_fn(outputs, targets)

                # Update validation loss
                valid_loss += loss.item()

        valid_loss /= len(valid_loader.dataset)

        return valid_loss