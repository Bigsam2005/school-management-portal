from django.conf import settings
from django.db import models

# Create your models here.

class SchoolMessage(models.Model):
    TARGET_ALL_PARENTS = "ALL_PARENTS"
    TARGET_CLASS = "CLASS"
    TARGET_PARENT = "PARENT"

    TARGET_CHOICES = [
        (TARGET_ALL_PARENTS, "All Parents"),
        (TARGET_CLASS, "Specific Class"),
        (TARGET_PARENT, "Individual Parent"),
    ]

    school = models.ForeignKey(
        "schools.School",
        on_delete=models.CASCADE,
        related_name="school_messages",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_school_messages",
    )

    subject = models.CharField(max_length=200)
    body = models.TextField()

    target_type = models.CharField(
        max_length=20,
        choices=TARGET_CHOICES,
        default=TARGET_ALL_PARENTS,
    )

    target_class = models.ForeignKey(
        "schools.SchoolClass",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="school_messages",
    )

    target_parent = models.ForeignKey(
        "students.ParentProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="school_messages",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.subject
