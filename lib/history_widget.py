from PyQt4.QtGui import *
from i18n import _

class HistoryWidget(QTreeWidget):

    def __init__(self, parent=None):
        QTreeWidget.__init__(self, parent)
        self.setColumnCount(2)
        self.setHeaderLabels([_("Amount"), _("To / From"), _("When")])
        self.setIndentation(0)

    def empty(self):
        self.clear()

    def append(self, address, amount, date):
        if address is None:
          address = "Unknown"
        if amount is None: 
          amount = "Unknown"
        if date is None:
          date = "Unknown"
        item = QTreeWidgetItem([amount, address, date])
        if float(amount) < 0:
          item.setForeground(0, QBrush(QColor("#BC1E1E")))
        self.insertTopLevelItem(0, item)

