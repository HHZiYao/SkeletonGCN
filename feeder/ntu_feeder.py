# !/usr/bin/env python                                                                                                                                                                                                                  
# -*- coding:utf-8 -*-

__author__ = 'FesianXu'
__date__ = 2018 / 6 / 26
__version__ = ''

import torch
import numpy as np 
import configs.ntu_configs as config 
import torch.utils.data
import copy
import dgl 

gc = config.GlobalConfig()

class NTU_Feeder(torch.utils.data.Dataset):
    def __init__(self,
                x_mode,
                t_mode,
                temp_mode='both',
                valid_length=None,
                self_connect=True):
        assert x_mode in ('xsub', 'xview')
        assert t_mode in ('train', 'test')
        assert temp_mode in ('forward', 'backward', 'both', None)
        
        self.x_mode = x_mode
        self.t_mode = t_mode
        self.valid_length = valid_length

        if x_mode == 'xsub':
            name_list = np.load(gc.ntu_xsub_loading_list).item()
        else:
            name_list = np.load(gc.ntu_xview_loading_list).item()

        if t_mode == 'train':
            self.datapool = name_list['train']
        else:
            self.datapool = name_list['test']

        self.missing_data = np.load(gc.ntu_missing_list)

    
    def __len__(self):
        if self.valid_length is None:
            return len(self.datapool)
        return self.valid_length

    def _get_file_name(self, setup_id, camera_id, person_id, repeat_id, action_id):
        return 'S{:0>3}C{:0>3}P{:0>3}R{:0>3}A{:0>3}.skeleton.npy'.format(setup_id, camera_id, person_id, repeat_id, action_id)
    
    def _check_view(self, camera_id, repeat_id):
        if camera_id == 1 and repeat_id == 1:
            return 3
        elif camera_id == 2 and repeat_id == 1:
            return 4
        elif camera_id == 3 and repeat_id == 1:
            return 0
        elif camera_id == 1 and repeat_id == 2:
            return 1
        elif camera_id == 2 and repeat_id == 2:
            return 0
        elif camera_id == 3 and repeat_id == 2:
            return 2
        else:
            raise ValueError('Invalid camera id and repeat id pair!')
    

    def _auto_multis(self, sample):
        data_numpy = np.zeros((gc.max_nframe,gc.num_joints,gc.base_num_channel,gc.max_person))
        if sample['nvalid'] > 1:
            data_numpy[:sample['body0'].shape[0],:,:,0] = sample['body0']
            data_numpy[:sample['body1'].shape[0],:,:,1] = sample['body1']
        else:
            data_numpy[:sample['body0'].shape[0],:,:,0] = sample['body0']
            data_numpy[:sample['body0'].shape[0],:,:,1] = sample['body0']

        data_numpy = np.transpose(data_numpy, axes=(2,0,1,3))
        return data_numpy


    def __getitem__(self, ind):
        while True:
            # some data are missing
            if self.datapool[ind] in self.missing_data:
                ind += 1
                continue
            
            sample = np.load(gc.ntu_data_path+self.datapool[ind]).item()
            label = int(sample['file_name'][17:20])-1
            x = self._auto_multis(sample)
            x = np.transpose(x, (3, 1,2,0))

            return x, label


def build_graph(temp_mode, max_nframe, self_connect=True, device=None, **kwargs):
    '''
    Build the topology of the graph computing in DGL
    '''
    num_joint = kwargs['num_joint']
    
    g = dgl.DGLGraph()
    g.add_nodes(num_joint * max_nframe)
    connect_bone = np.array(gc.connect_bone)
    connect_bone = np.concatenate((connect_bone, connect_bone[:, ::-1]), axis=0)
    # the undirected(bi-directed) connection
    
    expand_connect_bone = np.zeros((2*(num_joint-1)*max_nframe, 2))
    for eacht in range(max_nframe):
        expand_connect_bone[eacht*(2 * (num_joint-1)):(eacht+1)*(2 * (num_joint-1)), :] = connect_bone + eacht * num_joint
    g.add_edges(expand_connect_bone[:, 0], expand_connect_bone[:, 1])
    
    # spatial connection
    temporal_connect_forward = np.zeros( ((max_nframe-1)*gc.num_joints, 2) )
    for eachj in range(gc.num_joints):
        apoint = np.array([num_joint*i + eachj for i in range(0,max_nframe-1)])
        bpoint = np.array([num_joint*i + eachj for i in range(1,max_nframe)])
        temporal_connect_forward[eachj*(max_nframe-1):(eachj+1)*(max_nframe-1), :] = \
            np.stack((apoint, bpoint), axis=-1)
    
    temporal_connect_backward = temporal_connect_forward[:, ::-1]
    
    if temp_mode is not None:
        if temp_mode in ('forward', 'both'):
            g.add_edges(temporal_connect_forward[:,0], temporal_connect_forward[:,1])
        if temp_mode in ('backward', 'both'):
            g.add_edges(temporal_connect_backward[:,0], temporal_connect_backward[:,1])
        # temporal connection
    
    # add the self connection to cover the current node
    if self_connect:
        self_connect_config = list((i for i in range(gc.num_joints * max_nframe)))
        g.add_edges(self_connect_config, self_connect_config)
    
    degs = g.in_degrees().float()
    norm = torch.pow(degs, -0.5)
    norm[torch.isinf(norm)] = 0
    if device is not None:
        norm = norm.cuda(device)
    
    g.ndata['norm'] = norm.unsqueeze(1)
    # D^{-0.5}, symmetric normalization
    return g

def ntu_collate_fn(samples):
    '''
    args: 
        :samples: the mini-batch samples, still store in cpu, need to load to gpu
    return:
        :mat:    datamat in gpu, using dgl.batch, (N, M, T, V, C)
        :label:  labels
    '''
    nbatch = len(samples)
    graphs, labels = map(list, zip(*samples))
    labels = torch.tensor(labels)
    feature_np = torch.tensor(np.stack(graphs, axis=0)).float()
    return feature_np, labels

    
if __name__ == '__main__':
    feeder = NTU_Feeder(x_mode='xsub', t_mode='train')
    import time
    from torch.utils.data import DataLoader
    import copy


    graph = build_graph(temp_mode=None, max_nframe=300, num_joint=25)
    loader = NTU_Feeder(x_mode='xsub', t_mode='train')

    Data = DataLoader(loader, batch_size=4)
    for feature, label in Data:
        print(feature.shape)
        raise ValueError()
    