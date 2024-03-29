import tensorflow as tf
from tensorflow import keras
import tensorflow_probability as tfp
import numpy as np
import gym


CRITIC_LOSS_WEIGHT = 0.5
ENTROPY_LOSS_WEIGHT = 0.01
ENT_DISCOUNT_RATE = 0.995
BATCH_SIZE = 64
GAMMA = 0.99
CLIP_VALUE = 0.2
LR = 0.001

NUM_TRAIN_EPOCHS = 10

env = gym.make("CartPole-v0", render_mode='human')
num_actions = env.action_space.n

ent_discount_val = ENTROPY_LOSS_WEIGHT

class Model(keras.Model):
    def __init__(self, num_actions):
        super().__init__()
        self.num_actions = num_actions
        self.dense1 = keras.layers.Dense(64, activation='relu',
                                         kernel_initializer=keras.initializers.he_normal())
        self.dense2 = keras.layers.Dense(64, activation='relu',
                                         kernel_initializer=keras.initializers.he_normal())
        self.value = keras.layers.Dense(1)
        self.policy_logits = keras.layers.Dense(num_actions)

    def call(self, inputs):
        x = self.dense1(inputs)
        x = self.dense2(x)
        return self.value(x), self.policy_logits(x)

    def action_value(self, state):
        value, logits = self.call(state)
        dist = tfp.distributions.Categorical(logits=logits)
        action = dist.sample()
        return action, value


def critic_loss(discounted_rewards, value_est):
    return tf.cast(tf.reduce_mean(keras.losses.mean_squared_error(discounted_rewards, value_est)) * CRITIC_LOSS_WEIGHT, tf.float32)


def entropy_loss(policy_logits, ent_discount_val):
    probs = tf.nn.softmax(policy_logits)
    entropy_loss = -tf.reduce_mean(keras.losses.categorical_crossentropy(probs, probs))
    return entropy_loss * ent_discount_val


def actor_loss(advantages, old_probs, action_inds, policy_logits):
    probs = tf.nn.softmax(policy_logits)
    new_probs = tf.gather_nd(probs, action_inds)

    ratio = new_probs / old_probs

    policy_loss = -tf.reduce_mean(tf.math.minimum(
        ratio * advantages,
        tf.clip_by_value(ratio, 1.0 - CLIP_VALUE, 1.0 + CLIP_VALUE) * advantages
    ))
    return policy_loss


def train_model(action_inds, old_probs, states, advantages, discounted_rewards, optimizer, ent_discount_val):
    with tf.GradientTape() as tape:
        values, policy_logits = model.call(tf.stack(states))
        act_loss = actor_loss(advantages, old_probs, action_inds, policy_logits)
        ent_loss = entropy_loss(policy_logits, ent_discount_val)
        c_loss = critic_loss(discounted_rewards, values)
        tot_loss = act_loss + ent_loss + c_loss
    grads = tape.gradient(tot_loss, model.trainable_variables)
    optimizer.apply_gradients(zip(grads, model.trainable_variables))
    return tot_loss, c_loss, act_loss, ent_loss


def get_advantages(rewards, dones, values, next_value):
    discounted_rewards = np.array(rewards + [next_value[0]])

    for t in reversed(range(len(rewards))):
        discounted_rewards[t] = rewards[t] + GAMMA * discounted_rewards[t+1] * (1-dones[t])
    discounted_rewards = discounted_rewards[:-1]
    # advantages are bootstrapped discounted rewards - values, using Bellman's equation
    advantages = discounted_rewards - np.stack(values)[:, 0]
    # standardise advantages
    advantages -= np.mean(advantages)
    advantages /= (np.std(advantages) + 1e-10)
    # standardise rewards too
    discounted_rewards -= np.mean(discounted_rewards)
    discounted_rewards /= (np.std(discounted_rewards) + 1e-8)
    return discounted_rewards, advantages


model = Model(num_actions)
optimizer = keras.optimizers.Adam(learning_rate=LR)

num_steps = 10000000
state = env.reset()[0]
total_loss = None
for step in range(num_steps):
    rewards = []
    actions = []
    values = []
    states = []
    dones = []
    probs = []
    for _ in range(BATCH_SIZE):
        _, policy_logits = model(state.reshape(1, -1))

        action, value = model.action_value(state.reshape(1, -1))
        new_state, reward, done, _, _ = env.step(action.numpy()[0])

        actions.append(action)
        values.append(value[0])
        states.append(state)
        dones.append(done)
        probs.append(policy_logits)

        state = new_state

        if done:
            rewards.append(0.0)
            state = env.reset()[0]
        else:
            rewards.append(reward)

    _, next_value = model.action_value(state.reshape(1, -1))
    discounted_rewards, advantages = get_advantages(rewards, dones, values, next_value[0])

    actions = tf.squeeze(tf.stack(actions))
    probs = tf.nn.softmax(tf.squeeze(tf.stack(probs)))
    action_inds = tf.stack([tf.range(0, actions.shape[0]), tf.cast(actions, tf.int32)], axis=1)

    for epoch in range(NUM_TRAIN_EPOCHS):
        loss_tuple = train_model(action_inds, tf.gather_nd(probs, action_inds),
                                states, advantages, discounted_rewards, optimizer,
                                ent_discount_val)

    ent_discount_val *= ENT_DISCOUNT_RATE
