from typing import Optional, Tuple

import numpy as np
import numpy.typing as npt
import torch
from entity_gym.env import VecActionMask, VecCategoricalActionMask
from ragged_buffer import RaggedBufferI64
from torch import nn
from torch.distributions.categorical import Categorical
from torch.distributions.beta import Beta

from rogue_net.ragged_tensor import RaggedTensor


class CategoricalActionHead(nn.Module):
    def __init__(self, d_model: int, n_choice: int) -> None:
        super().__init__()
        
        self.beta = False

        self.d_model = d_model
        if self.beta:
            self.n_choice = 2
            self.proj = layer_init(nn.Linear(d_model, 2), std=0.01)
        else:
            self.n_choice = n_choice       
            self.proj = layer_init(nn.Linear(d_model, n_choice), std=0.01)

    def forward(
        self,
        x: RaggedTensor,
        index_offsets: RaggedBufferI64,
        mask: VecActionMask,
        prev_actions: Optional[RaggedBufferI64],
    ) -> Tuple[
        torch.Tensor, npt.NDArray[np.int64], torch.Tensor, torch.Tensor, torch.Tensor
    ]:
        assert isinstance(
            mask, VecCategoricalActionMask
        ), f"Expected CategoricalActionMaskBatch, got {type(mask)}"

        device = x.data.device
        lengths = mask.actors.size1()
        if len(mask.actors) == 0:
            return (
                torch.zeros((0), dtype=torch.int64, device=device),
                lengths,
                torch.zeros((0), dtype=torch.float32, device=device),
                torch.zeros((0), dtype=torch.float32, device=device),
                torch.zeros((0, self.n_choice), dtype=torch.float32, device=device),
            )

        actors = (
            torch.tensor((mask.actors + index_offsets).as_array())
            .to(x.data.device)
            .squeeze(-1)
        )
        actor_embeds = x.data[actors]
        logits = self.proj(actor_embeds)

        # Apply masks from the environment
        if mask.mask is not None and mask.mask.size0() > 0:
            reshaped_masks = torch.tensor(
                mask.mask.as_array().reshape(logits.shape)
            ).to(x.data.device)
        
        beta_flag = self.beta
        scaler = np.iinfo(np.int64).max

        if beta_flag:
            logits = (logits ** 2 + 1).to('cpu')
            dist = Beta(logits[:,0],logits[:,1])
        else:
            dist = Categorical(logits=logits)
        
        if prev_actions is None:
            if beta_flag:
                action = dist.rsample()
            else:
                action = dist.sample()
        else:
            if beta_flag:
                action = torch.tensor(prev_actions.as_array().squeeze(-1)).to('cpu') / scaler
            else:
                action = torch.tensor(prev_actions.as_array().squeeze(-1)).to(x.data.device)
#        print(action)
        #logprob = dist.log_prob(action)
        if beta_flag:
            logprob = torch.zeros_like(action).to('cpu')
            for i in range(action.shape[0]):
                logprob[i] = dist.log_prob(action[i])[i]
            logprob = logprob.to(x.data.device)
        else:
            logprob = dist.log_prob(action)
        entropy = dist.entropy()
        #logprob =  torch.zeros(action.shape)
        #entropy =  torch.zeros(action.shape)

        if beta_flag:
            return_logits = logits.to(x.data.device)
            action_return = action * scaler
            action_return = action_return.to(torch.int64).to(x.data.device)
        else:
            action_return = action
            return_logits = dist.logits

        return action_return, lengths, logprob, entropy, return_logits 



def layer_init(
    layer: nn.Module,
    std: float = np.sqrt(2),
    bias_const: float = 0.0,
) -> nn.Module:
    torch.nn.init.orthogonal_(layer.weight, std)
    torch.nn.init.constant_(layer.bias, bias_const)  # type: ignore
    return layer
