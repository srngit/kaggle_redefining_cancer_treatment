import logging
import time
import tensorflow as tf
from tensorflow.python.training import session_run_hook
from tensorflow.python.training.basic_session_run_hooks import StopAtStepHook
from .task_spec import get_task_spec, get_logs_path


class Trainer(session_run_hook.SessionRunHook):
    """
    Default class for training. This class extends from SessionRunHook and it is added to the hooks
    in the MonitoredTrainingSession as one of the chief_only_hooks
    """

    def __init__(self, log_dir, max_time=None, num_steps=None, max_steps=None,
                 save_checkpoint_secs=600, save_summaries_steps=100, log_step_count_steps=100,
                 monitored_training_session_config=None, task_spec=None, dataset=None):
        """
        :param str log_dir: directory where logs are stored
        :param int max_time: max time to run the training, by default isNone to run indefinitely
        :param int num_steps: number of steps to run the current training process, by default is
        None to run indefinitely
        :param int max_steps: step after which to stop, by default is None to run indefinitely
        :param int save_checkpoint_secs: seconds to save the checkpoints
        :param int save_summaries_steps: steps to save the summaries
        :param int log_step_count_steps: steps to log the steps_count
        :param tf.ConfigProto monitored_training_session_config: an instance of tf.ConfigProto,
        the configuration for the monitored training session
        :param TFDataSet dataset: the dataset for this trainer
        """
        self.log_dir = get_logs_path(log_dir)
        self.save_checkpoint_secs = save_checkpoint_secs
        self.save_summaries_steps = save_summaries_steps
        self.log_step_count_steps = log_step_count_steps
        self.monitored_training_session_config = monitored_training_session_config
        self.max_time = max_time
        self.num_steps = num_steps
        self.max_steps = max_steps
        logging.info('Log dir: {}'.format(self.log_dir))
        if task_spec is None:
            self.task_spec = get_task_spec()
        else:
            self.task_spec = task_spec
        self.dataset = dataset

    def run(self, batch_size, epochs):
        """
        Starts the training
        :param batch_size: the batch size
        :param epochs: the number of epochs to run
        """
        if self.task_spec.is_master() and self.task_spec.index > 0:
            raise ValueError('Only one replica of master expected')

        if self.task_spec.cluster_spec:
            server = tf.train.Server(self.task_spec.cluster_spec, job_name=self.task_spec.job_name,
                                     task_index=self.task_spec.index)
            if self.task_spec.is_ps():
                server.join()
            target = server.target
            current_device = '/job:{}/task:{}'.format(self.task_spec.job_name, self.task_spec.index)
            ps_devices = '/job:ps'
            device_fn = tf.train.replica_device_setter(ps_device=ps_devices,
                                                       worker_device=current_device,
                                                       cluster=self.task_spec.cluster_spec)
            device_dataset_fn = current_device
        else:
            target = ''
            device_fn = ''
            device_dataset_fn = ''

        with tf.Graph().as_default():
            logging.info('Creating graph...')
            if self.dataset is None:
                dataset_tensor = None
            else:
                with tf.device(device_dataset_fn):
                    dataset_tensor = self.dataset.read(batch_size=batch_size, num_epochs=epochs,
                                                       shuffle=True, task_spec=self.task_spec)
            with tf.device(device_fn):
                graph_data = self.create_graph(dataset_tensor, batch_size)

            hooks, chief_only_hooks = self.create_hooks(graph_data)
            if hooks is None:
                hooks = []
            if chief_only_hooks is None:
                chief_only_hooks = []
            chief_only_hooks.append(self)
            if self.max_time and self.max_time > 0:
                hooks.append(StopAtTimeHook(self.max_time))
            if (self.max_steps or self.num_steps) and (self.max_steps > 0 or self.num_steps > 0):
                hooks.append(StopAtStepHook(num_steps=self.num_steps, last_step=self.max_steps))

            self.is_chief = self.task_spec.is_chief()
            logging.info('Creating MonitoredTrainingSession...')
            with tf.train.MonitoredTrainingSession(master=target, is_chief=self.is_chief,
                        checkpoint_dir=self.log_dir, save_checkpoint_secs=self.save_checkpoint_secs,
                        save_summaries_steps=self.save_summaries_steps,
                        log_step_count_steps=self.log_step_count_steps,
                        config=self.monitored_training_session_config, hooks=hooks,
                        chief_only_hooks=chief_only_hooks) as sess:

                logging.info('Starting training...')

                while not sess.should_stop():
                    self.step(sess, graph_data)

    def create_graph(self, dataset_tensor, batch_size):
        """
        Function to create the graph
        :param dataset_tensor: the tensor with the data created with the dataset.
        :param batch_size: the batch_size used when run() is called
        :return: Information related with the graph. It will be passed to other functions
        """
        raise NotImplementedError('Should have implemented this')

    def create_hooks(self, graph_data):
        """
        Creates the hooks for the session. This function is called after the graph is created and
        before the session is created.
        :param graph_data: the graph data returned create_graph
        :return: A tuple with two lists of hooks or none. First list if the hooks for all nodes and
        the second list are the hooks only for the master node.
        """
        return [], []

    def step(self, session, graph_data):
        """
        Function to run one time step.
        :param session: the session
        :param graph_data: the graph data returned in create_graph
        """
        raise NotImplementedError('Should have implemented this')


class StopAtTimeHook(session_run_hook.SessionRunHook):
    """Hook that requests stop after a specified time."""

    def __init__(self, time_running):
        """
        :param int time_running: Maximum time running
        """
        self._time_running = time_running

    def begin(self):
        self._end_time = time.time() + self._time_running

    def after_run(self, run_context, run_values):
        if time.time() > self._end_time:
            run_context.request_stop()
