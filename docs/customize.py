"""3.AutoGluon Example on Customized Training
==========================================

This is a tutorial for adapting customized training script into AutoGluon searchable.

**Import the basic packages, such as numpy, mxnet and gluoncv.**
"""

import os
import time
import logging
import numpy as np

import mxnet as mx
from mxnet import gluon, init
from gluoncv.model_zoo import get_model

################################################################
# **Define a function to load the given network parameters**
#

def get_network(num_classes, ctx):
    finetune_net = get_model('densenet169', pretrained=False)
    finetune_net.collect_params().load('densenet169-0000.params')
    # change the last fully connected layer to match the number of classes
    with finetune_net.name_scope():
        finetune_net.output = gluon.nn.Dense(num_classes)
    # initialize and context
    finetune_net.output.initialize(init.Xavier(), ctx=ctx)
    finetune_net.collect_params().reset_ctx(ctx)
    finetune_net.hybridize()
    return finetune_net

################################################################
# **Define a function for dataset meta data**
#

def get_dataset_meta(dataset, basedir='./datasets'):
    if dataset.lower() == 'apparel':
        num_classes = 18
        rec_train = os.path.join(basedir, 'Apparel_train.rec')
        rec_train_idx = os.path.join(basedir, 'Apparel_train.idx')
        rec_val = os.path.join(basedir, 'Apparel_test.rec')
        rec_val_idx = os.path.join(basedir, 'Apparel_test.idx')
    else:
        raise NotImplemented
    return num_classes, rec_train, rec_train_idx, rec_val, rec_val_idx

################################################################
# **Define the test/evaluation function**
#

def test(net, val_data, ctx, batch_fn):
    metric = mx.metric.Accuracy()
    val_data.reset()
    for i, batch in enumerate(val_data):
        data, label = batch_fn(batch, ctx)
        outputs = [net(X) for X in data]
        metric.update(label, outputs)

    return metric.get()

################################################################
# **Define the training loop** This is a 40-line normal finetuning script, with only basic components:
#

def train_loop(args, reporter):
    lr_steps = [int(args.epochs*0.75), np.inf]
    ctx = [mx.gpu(i) for i in range(args.num_gpus)] if args.num_gpus > 0 else [mx.cpu()]

    num_classes, rec_train, rec_train_idx, rec_val, rec_val_idx = get_dataset_meta(args.dataset)
    finetune_net = get_network(num_classes, ctx)

    train_data, val_data, batch_fn = get_data_rec(
            args.input_size, args.crop_ratio, rec_train, rec_train_idx,
            rec_val, rec_val_idx, args.batch_size, args.num_workers,
            args.jitter_param, args.max_rotate_angle)

    trainer = gluon.Trainer(finetune_net.collect_params(), 'sgd', {
                            'learning_rate': args.lr, 'momentum': args.momentum, 'wd': args.wd})
    metric = mx.metric.Accuracy()
    L = gluon.loss.SoftmaxCrossEntropyLoss()

    lr_counter = 0
    for epoch in range(args.epochs):
        if epoch == lr_steps[lr_counter]:
            print('Decreasing LR to ', trainer.learning_rate*args.lr_factor)
            trainer.set_learning_rate(trainer.learning_rate*args.lr_factor)
            lr_counter += 1

        train_data.reset()
        metric.reset()
        for i, batch in enumerate(train_data):
            data, label = batch_fn(batch, ctx)
            with mx.autograd.record():
                outputs = [finetune_net(X) for X in data]
                loss = [L(yhat, y) for yhat, y in zip(outputs, label)]
            for l in loss:
                l.backward()

            trainer.step(args.batch_size)
            metric.update(label, outputs)

        _, train_acc = metric.get()
        _, val_acc = test(finetune_net, val_data, ctx, batch_fn)

        if reporter is not None:
            # reporter enables communicatons with autogluon
            reporter(epoch=epoch, accuracy=val_acc)
        else:
            print('[Epoch %d] Train-acc: %.3f | Val-acc: %.3f' %
                  (epoch, train_acc, val_acc))

################################################################
# **How to convert any training function to enable autogluon HPO?**
#

import autogluon as ag
from autogluon import autogluon_register_args
from autogluon.utils.mxutils import get_data_rec

@autogluon_register_args(
    dataset='apparel',
    resume=False,
    epochs=ag.ListSpace(80, 40, 120),
    lr=ag.LogLinearSpace(1e-4, 1e-2),
    lr_factor=ag.LogLinearSpace(0.1, 1),
    batch_size=256,
    momentum=0.9,
    wd=ag.LogLinearSpace(1e-5, 1e-3),
    num_gpus=8,
    num_workers=30,
    input_size=ag.ListSpace(224, 256),
    crop_ratio=0.875,
    jitter_param=ag.LinearSpace(0.1, 0.4),
    max_rotate_angle=ag.IntSpace(0, 10),
    remote_file='remote_ips.txt',
)
def train_finetune(args, reporter):
    return train_loop(args, reporter)

################################################################
# **Create the searcher and scheduler**
#

searcher = ag.searcher.RandomSampling(train_finetune.cs)
args =  train_finetune.args
myscheduler = ag.distributed.DistributedFIFOScheduler(train_finetune,args,
                                                      resource={'num_cpus': 16, 'num_gpus': args.num_gpus},
                                                      searcher=searcher,
                                                      checkpoint='./{}/checkerpoint.ag'.format(args.dataset),
                                                      num_trials=25,
                                                      resume=args.resume,
                                                      time_attr='epoch',
                                                      reward_attr="accuracy")
print(myscheduler)


################################################################
# **Run the experiments**
#

myscheduler.run()

################################################################
# **Join the tasks and gather the results**
#

myscheduler.join_tasks()
myscheduler.get_training_curves(plot=True,use_legend=False)
print('The Best Configuration and Accuracy are: {}, {}'.format(myscheduler.get_best_config(),
                                                               myscheduler.get_best_reward()))