from gymnasium import Env
from gymnasium.spaces import Discrete
import threading
import rospy
import rosgraph
from co_learning_messages.msg import secondary_task_message,hand_pose
from std_msgs.msg import Bool

class CoLearn(Env):
    def __init__(self):
        '''
        Defines action space containing 8 actions (0-7)
        - Move to home                      : 0, 0
        - Initiate handover when human asks : 1, 1
        - Wait for state_change             : 1, 2
        - Go to Serve                       : 2, 3
        - Go to Drop                        : 2, 4
        - Open hand                         : 3, 5
        - Open hand partialy                : 3, 6
        - Wait 0.5 seconds                  : 3, 7
        Where the first number indicates the phase

        
        Defines an observation space containing 17 states (0-16)
        - Home                                          : 0, 0
        - (human asks, hand in workspace)               : 1, 1
        - (human asks, hand not in workspace)           : 1, 2
        - (wait for state change, hand in workspace)    : 1, 3
        - (wait for state change, hand not in workspace): 1, 4
        - (Hand_h: serve, robot: serve)                 : 2, 5
        - (Hand_h: serve, robot: drop)                  : 2, 6
        - (Hand_h: drop, robot: serve)                  : 2, 7
        - (hand_h: drop, robot: drop)                   : 2, 8
        - (Hand_h: unknown, robot: serve)               : 2, 9
        - (Hand_h: unknown, robot: drop)                : 2, 10
        - (Human_input, Hand open)                      : 3, 11
        - (Human_input, Hand partial)                   : 3, 12
        - (Human_input, Wait 0.5 seconds)               : 3, 13
        - (No human_input, Hand open)                   : 3, 14
        - (No human_input, Hand partial)                : 3, 15
        - (No human_input, Wait 0.5 seconds)            : 3, 16
        '''

        
        self.action_size = 8
        self.action_space = Discrete(self.action_size)
        
        self.observation_size = 17
        self.observation_space = Discrete(self.observation_size)

        self.phase_size = 5
        self.phase = 0

        self.state_size = 17
        self.state = 0 

        self.max_episode_length = 100
        self.episode_length = self.max_episode_length 

        self.info = {'valid':None,
                     'phase':0}
        
        self.phase_0_state = 0
        self.phase_1_state = 0
        self.phase_2_state = 0
        self.phase_3_state = 0

        self.condition = threading.Condition()
        self.relevant_message = None

        self.successfull_handover = 0
        self.human_input = False
        self.orientation = 'None'

        self.initialise_ros()

    def status_callback(self, msg):
        with self.condition:
            if msg.handover_successful != 0:  # Check if handover_successful is set to either -1 or 1
                self.relevant_part = {
                    'handover_successful': msg.handover_successful,
                    'time_left': msg.time_left
                }
                self.condition.notify()

    def hand_pose_callback(self,msg):
        self.orientation = msg.orientation

    def human_input_callback(self, msg):
        self.human_input = msg.data

    def initialise_ros(self):
        self.ros_running = rosgraph.is_master_online()
        if self.ros_running:
            rospy.Subscriber('Task_status',secondary_task_message,self.status_callback)
            rospy.Subscriber('hand_pose',hand_pose,self.hand_pose_callback)
            rospy.Subscriber('human_input',Bool,self.human_input_callback)
            self.rate=rospy.Rate(1)
            try:
                rospy.init_node('Environment', anonymous=True)
            except:
                rospy.logwarn("Cannot initialize node 'Environment' as it has already been initialized at the top level as a ROS node.")
        else:
            print("ROS is offline! Environment proceeds in offline mode")

        self.relevant_part = {'handover_successful': 0, 'time_left': 0}

    def check_valid_action(self,action):
        if self.phase == 0:
            return action in [1,2]
        elif self.phase == 1:
            return action in [3,4]
        elif self.phase == 2:
            return action in [5,6,7]
        elif self.phase == 3:
            return action == 0
        else: return False

    def update_state(self,action):
        if self.phase == 0:
            if action == 1:
                return 2 if self.orientation == 'None' else 1 # self.orientation == 'None' means that the hand is not in the workspace
            elif action == 2:
                return 4 if self.orientation == 'None' else 2
        elif self.phase == 1:
            if action == 3:
                if self.orientation == 'Serve':
                    return 5
                elif self.orientation == 'Drop':
                    return 7
                else: return 9
            if action == 4:
                if self.orientation == 'Serve':
                    return 6
                elif self.orientation == 'Drop':
                    return 8
                else: return 10
        elif self.phase == 2:
            if self.human_input:
                if action == 5:
                    return 11
                elif action == 6:
                    return 12
                elif action == 7:
                    return 13
            elif not self.human_input:
                if action ==5:
                    return 14
                if action == 6:
                    return 15
                if action == 7:
                    return 16
        elif self.phase == 3:
            return action # 0 for valid transition
                

    def step(self, action=None):
        action = self.action_space.sample() if action is None else action
        valid_transition = self.check_valid_action(action)

        self.episode_length -= 1
        terminated = self.episode_length <= 0 or (self.phase == 3 and valid_transition)
       
        if valid_transition:
            self.state = self.update_state(action)
            if terminated:
                self.phase = 0
            else:
                self.phase += 1
            self.save_previous_state()

        reward = self.obtain_reward()
        
        self.info['valid'] = valid_transition
        self.info['phase'] = self.phase

        return self.state, reward, terminated, self.info 

    def obtain_reward(self):
        reward = 0
        if self.ros_running:
            if self.phase == 4:
                with self.condition:
                    while self.relevant_part is None or 'handover_successful' not in self.relevant_part:
                        self.condition.wait()  # Ensures we obtain the reward only as soon as the handover is successful (or failed)
                    if self.relevant_part['handover_successful'] == 1:
                        reward += self.phase_size * 10 # Reward for finishing is 10 (for each state)
                        reward += self.phase_size * self.relevant_part['time_left'] # Dynamic reward based on performance
                    else:
                        reward -= self.phase_size * 10 
            
        else:
            if self.phase_1_state == 2 and self.phase_2_state == 9 and self.phase_3_state == 14:
                reward += 20
        return reward
    
    def save_previous_state(self):
        if self.phase == 0:
            self.phase_0_state = self.state
        if self.phase == 1:
            self.phase_1_state = self.state
        if self.phase == 2:
            self.phase_2_state = self.state
        if self.phase == 3:
            self.phase_3_state = self.state
        
    
    def reset(self):
        self.state = 0
        self.phase = 0
        self.episode_length = self.max_episode_length 
        return self.state, self.phase

    def close(self):
        pass



