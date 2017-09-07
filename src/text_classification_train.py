import tensorflow as tf
import csv
import time
from datetime import timedelta
import sys
from tensorflow.python.training import training_util
from tensorflow.contrib import slim
from configuration import *
import trainer
import metrics
from text_classification_test import TextClassificationEvaluator, TextClassificationTest
from text_classification_dataset import TextClassificationDataset


class TextClassificationTrainer(trainer.Trainer):
    """
    Helper class to run the training and create the model for the training. See trainer.Trainer for
    more details.
    """

    def __init__(self, dataset, text_classification_model, epochs=TC_EPOCHS,
                 batch_size=TC_BATCH_SIZE, logdir=DIR_TC_LOGDIR):
        self.dataset = dataset
        self.text_classification_model = text_classification_model
        self.batch_size = batch_size
        max_steps = epochs * dataset.get_size() / batch_size
        super(TextClassificationTrainer, self).__init__(logdir, max_steps=max_steps)

    def _load_embeddings(self, vocabulary_size, embeddings_size):
        embeddings = []
        embeddings_file = 'embeddings_{}_{}'.format(vocabulary_size, embeddings_size)
        with open(os.path.join(DIR_DATA_WORD2VEC, embeddings_file), 'r') as file:
            reader = csv.reader(file, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
            for row in reader:
                embeddings.append([float(r) for r in row])
        return embeddings

    def model(self, vocabulary_size=VOCABULARY_SIZE, embeddings_size=EMBEDDINGS_SIZE,
              output_classes=9):
        # embeddings
        embeddings = self._load_embeddings(vocabulary_size, embeddings_size)

        # global step
        self.global_step = training_util.get_or_create_global_step()

        # inputs
        self.inputs_text, self.expected_labels = self.dataset.read(self.batch_size, shuffle=True)

        # model
        with slim.arg_scope(self.text_classification_model.model_arg_scope()):
            outputs = self.text_classification_model.model(self.inputs_text, output_classes,
                                                           embeddings=embeddings)

        # loss
        targets = self.text_classification_model.targets(self.expected_labels, output_classes)
        self.loss = self.text_classification_model.loss(targets, outputs)
        tf.summary.scalar('loss', self.loss)

        # learning rate
        self.optimizer, self.learning_rate = \
            self.text_classification_model.optimize(self.loss, self.global_step)
        if self.learning_rate is not None:
            tf.summary.scalar('learning_rate', self.learning_rate)

        # metrics
        self.metrics = metrics.single_label(outputs['prediction'], tf.squeeze(targets))

        # saver to save the model
        self.saver = tf.train.Saver()
        # check a nan value in the loss
        self.loss = tf.check_numerics(self.loss, 'loss is nan')

        return None

    def create_graph(self):
        return self.model()

    def train_step(self, session, graph_data):
        lr, _, loss, step, metrics = \
            session.run([self.learning_rate, self.optimizer, self.loss, self.global_step,
                         self.metrics])
        if self.is_chief and time.time() > self.print_timestamp + 5 * 60:
            self.print_timestamp = time.time()
            elapsed_time = str(timedelta(seconds=time.time() - self.init_time))
            m = 'step: {}  loss: {:0.4f}  learning_rate = {:0.6f}  elapsed seconds: {}  ' \
                'precision: {}  recall: {}  accuracy: {}'
            print(m.format(step, loss, lr, elapsed_time,
                           metrics['precision'], metrics['recall'], metrics['accuracy']))

    def after_create_session(self, session, coord):
        self.init_time = time.time()
        self.print_timestamp = time.time()


def main(model, name):
    if len(sys.argv) > 1 and sys.argv[1] == 'test':
        dataset = TextClassificationDataset(type='train', sentence_split=True)
        tester = TextClassificationTest(dataset=dataset, text_classification_model=model,
                                        logdir='{}_{}'.format(DIR_TC_LOGDIR, name))
        tester.run()
    elif len(sys.argv) > 1 and sys.argv[1] == 'eval':
        dataset = TextClassificationDataset(type='test', sentence_split=True)
        evaluator = TextClassificationEvaluator(dataset=dataset, text_classification_model=model,
                                                logdir='{}_{}'.format(DIR_TC_LOGDIR, name))
        evaluator.run()
    else:
        dataset = TextClassificationDataset(type='train', sentence_split=True)
        trainer = TextClassificationTrainer(dataset=dataset, text_classification_model=model,
                                            logdir='{}_{}'.format(DIR_TC_LOGDIR, name))
        trainer.run()
